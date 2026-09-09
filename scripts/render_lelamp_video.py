#!/usr/bin/env python3
"""Render a silent, no-text kinematic video from the original LeLamp model.

This module intentionally stays independent from the AILamp/LeLamp hardware
runtime.  It reads the original MJCF and recordings, writes only requested
media/provenance artifacts, and drives no devices or external services.
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import datetime as dt
import hashlib
import json
import math
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import mujoco
import numpy as np
from PIL import Image


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL_PATH = WORKSPACE_ROOT / "LeLamp" / "simulation" / "scene.xml"
DEFAULT_RECORDINGS_DIR = WORKSPACE_ROOT / "lelamp_runtime" / "lelamp" / "recordings"
DEFAULT_OUTPUT_PATH = (
    WORKSPACE_ROOT / "AILamp" / "output" / "video" / "LeLamp_MuJoCo_Preview_NoText.mp4"
)
DEFAULT_PREVIEW_NAME = "LeLamp_MuJoCo_Preview_Montage_NoText.png"
DEFAULT_METADATA_NAME = "preview_metadata.json"
DEFAULT_README_NAME = "README.md"
ROOT_JOINT_NAME = "lamparm__base_elbow_freejoint"
BASE_BODY_NAME = "scs215_v5"
BASE_MESH_NAME = "lamp_base"

# The dictionary order is the canonical CSV/target column order.  The actual
# actuator array is deliberately not used; in the original MJCF it is 2,1,3,4,5.
SERVO_TO_JOINT = {
    "base_yaw": "1",
    "base_pitch": "2",
    "elbow_pitch": "3",
    "wrist_roll": "4",
    "wrist_pitch": "5",
}


@dataclass(frozen=True)
class MotionSpec:
    name: str
    max_duration_seconds: float | None = None


DEFAULT_MOTION_SPECS = (
    MotionSpec("wake_up"),
    MotionSpec("curious"),
    MotionSpec("nod"),
    MotionSpec("scanning"),
    MotionSpec("headshake"),
    MotionSpec("idle", max_duration_seconds=8.0),
)

NORMALIZATION_EVIDENCE_PATHS = (
    WORKSPACE_ROOT
    / "lelamp_runtime"
    / "lelamp"
    / "follower"
    / "config_lelamp_follower.py",
    WORKSPACE_ROOT
    / "lelamp_runtime"
    / "lelamp"
    / "leader"
    / "config_lelamp_leader.py",
)


@dataclass(frozen=True)
class Recording:
    name: str
    source_path: Path
    servo_names: tuple[str, ...]
    timestamps: np.ndarray
    normalized_positions: np.ndarray

    @property
    def duration_seconds(self) -> float:
        return float(self.timestamps[-1])

    @property
    def row_count(self) -> int:
        return int(self.timestamps.size)


@dataclass(frozen=True)
class JointBinding:
    servo_name: str
    joint_name: str
    joint_id: int
    qpos_address: int
    lower: float
    upper: float
    actuator_id: int


@dataclass(frozen=True)
class TimelineSegment:
    kind: str
    name: str
    start_frame: int
    end_frame: int
    source_duration_seconds: float | None = None
    included_duration_seconds: float | None = None


@dataclass(frozen=True)
class RecordingUsage:
    name: str
    source_path: Path
    row_count: int
    source_duration_seconds: float
    included_duration_seconds: float
    normalized_min: tuple[float, ...]
    normalized_max: tuple[float, ...]


@dataclass(frozen=True)
class Timeline:
    fps: int
    times: np.ndarray
    targets: np.ndarray
    segments: tuple[TimelineSegment, ...]
    recordings: tuple[RecordingUsage, ...]

    @property
    def frame_count(self) -> int:
        return int(self.targets.shape[0])

    @property
    def duration_seconds(self) -> float:
        """Simulation timestamp of the final frame."""
        return float(self.times[-1])

    @property
    def encoded_duration_seconds(self) -> float:
        """Nominal media duration when every frame lasts exactly 1/fps."""
        return self.frame_count / self.fps


@dataclass(frozen=True)
class BaseReference:
    position: np.ndarray
    rotation: np.ndarray
    root_joint_id: int
    root_qpos_address: int
    base_body_id: int
    root_joint_name: str = ROOT_JOINT_NAME
    base_body_name: str = BASE_BODY_NAME


@dataclass(frozen=True)
class AnchorDiagnostics:
    translation_drift_m: float
    orientation_drift_rad: float


@dataclass(frozen=True)
class GeometryAnalysis:
    visible_lower: np.ndarray
    visible_upper: np.ndarray
    base_mesh_lower: np.ndarray
    base_mesh_upper: np.ndarray
    maximum_translation_drift_m: float
    maximum_orientation_drift_rad: float


@dataclass(frozen=True)
class CameraConfiguration:
    lookat: np.ndarray
    distance: float
    azimuth: float
    elevation: float


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_file(path: Path, description: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"{description} not found: {resolved}")
    return resolved


def load_recording(path: Path) -> Recording:
    """Load one strict LeLamp CSV and normalize its timestamps to zero."""
    source_path = _require_file(Path(path), "recording")
    expected_fields = ["timestamp", *(f"{name}.pos" for name in SERVO_TO_JOINT)]
    timestamps: list[float] = []
    positions: list[list[float]] = []

    with source_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != expected_fields:
            raise ValueError(
                f"Unexpected columns in {source_path}: {reader.fieldnames}; "
                f"expected {expected_fields}"
            )
        for row_number, row in enumerate(reader, start=2):
            try:
                timestamp = float(row["timestamp"])
                values = [float(row[f"{name}.pos"]) for name in SERVO_TO_JOINT]
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Non-numeric CSV value in {source_path}:{row_number}") from exc
            if not math.isfinite(timestamp) or not np.isfinite(values).all():
                raise ValueError(f"Non-finite CSV value in {source_path}:{row_number}")
            timestamps.append(timestamp)
            positions.append(values)

    if len(timestamps) < 2:
        raise ValueError(f"Recording must contain at least two rows: {source_path}")
    timestamp_array = np.asarray(timestamps, dtype=np.float64)
    if not np.all(np.diff(timestamp_array) > 0.0):
        raise ValueError(f"Recording timestamps must be strictly increasing: {source_path}")
    timestamp_array -= timestamp_array[0]
    position_array = np.asarray(positions, dtype=np.float64)
    return Recording(
        name=source_path.stem,
        source_path=source_path,
        servo_names=tuple(SERVO_TO_JOINT),
        timestamps=timestamp_array,
        normalized_positions=position_array,
    )


def resolve_joint_bindings(model: mujoco.MjModel) -> dict[str, JointBinding]:
    """Resolve every CSV servo to its MJCF joint by name, never array order."""
    bindings: dict[str, JointBinding] = {}
    for servo_name, joint_name in SERVO_TO_JOINT.items():
        joint_id = int(mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, joint_name))
        if joint_id < 0:
            raise ValueError(f"MJCF joint not found for {servo_name}: {joint_name}")
        if int(model.jnt_type[joint_id]) != int(mujoco.mjtJoint.mjJNT_HINGE):
            raise ValueError(f"Expected hinge joint for {servo_name}: {joint_name}")
        if not bool(model.jnt_limited[joint_id]):
            raise ValueError(f"Expected finite limits for {servo_name}: {joint_name}")
        lower, upper = (float(value) for value in model.jnt_range[joint_id])
        if not (math.isfinite(lower) and math.isfinite(upper) and lower < upper):
            raise ValueError(f"Invalid joint limits for {servo_name}: {(lower, upper)}")

        actuator_id = int(
            mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, joint_name)
        )
        if actuator_id < 0:
            raise ValueError(f"MJCF actuator not found for {servo_name}: {joint_name}")
        actuator_joint_id = int(model.actuator_trnid[actuator_id, 0])
        if actuator_joint_id != joint_id:
            raise ValueError(
                f"Actuator {joint_name} drives joint id {actuator_joint_id}, expected {joint_id}"
            )

        bindings[servo_name] = JointBinding(
            servo_name=servo_name,
            joint_name=joint_name,
            joint_id=joint_id,
            qpos_address=int(model.jnt_qposadr[joint_id]),
            lower=lower,
            upper=upper,
            actuator_id=actuator_id,
        )
    return bindings


def normalized_to_radians(
    values: float | np.ndarray, lower: float, upper: float
) -> float | np.ndarray:
    """Map LeRobot RANGE_M100_100 values linearly onto an MJCF joint range."""
    array = np.asarray(values, dtype=np.float64)
    converted = lower + ((np.clip(array, -100.0, 100.0) + 100.0) / 200.0) * (
        upper - lower
    )
    if array.ndim == 0:
        return float(converted)
    return converted


def _smooth_recording_positions(
    recording: Recording, window_seconds: float = 0.18
) -> np.ndarray:
    """Apply a deterministic Hann low-pass filter while preserving endpoints."""
    if window_seconds <= 0.0:
        return recording.normalized_positions.copy()
    median_step = float(np.median(np.diff(recording.timestamps)))
    window = max(3, int(round(window_seconds / median_step)))
    if window % 2 == 0:
        window += 1
    if window >= recording.row_count:
        window = recording.row_count - 1 if recording.row_count % 2 == 0 else recording.row_count
    if window < 3:
        return recording.normalized_positions.copy()

    kernel = np.hanning(window)
    kernel /= kernel.sum()
    pad = window // 2
    result = np.empty_like(recording.normalized_positions)
    for column in range(recording.normalized_positions.shape[1]):
        values = recording.normalized_positions[:, column]
        padded = np.pad(values, (pad, pad), mode="edge")
        result[:, column] = np.convolve(padded, kernel, mode="valid")
    result[0] = recording.normalized_positions[0]
    result[-1] = recording.normalized_positions[-1]
    return result


def _resample_recording(
    recording: Recording,
    bindings: Mapping[str, JointBinding],
    fps: int,
    max_duration_seconds: float | None,
) -> tuple[np.ndarray, float]:
    duration = recording.duration_seconds
    if max_duration_seconds is not None:
        if not math.isfinite(max_duration_seconds) or max_duration_seconds <= 0.0:
            raise ValueError("max_duration_seconds must be finite and positive")
        duration = min(duration, max_duration_seconds)
    if duration <= 0.0:
        raise ValueError(f"Recording has no positive duration: {recording.source_path}")

    sample_count = max(2, int(round(duration * fps)) + 1)
    sample_times = np.linspace(0.0, duration, sample_count, dtype=np.float64)
    smoothed = _smooth_recording_positions(recording)
    normalized = np.column_stack(
        [
            np.interp(sample_times, recording.timestamps, smoothed[:, column])
            for column in range(smoothed.shape[1])
        ]
    )
    targets = np.empty_like(normalized)
    for column, servo_name in enumerate(SERVO_TO_JOINT):
        binding = bindings[servo_name]
        targets[:, column] = normalized_to_radians(
            normalized[:, column], binding.lower, binding.upper
        )
        targets[:, column] = np.clip(targets[:, column], binding.lower, binding.upper)
    return targets, duration


def _quintic_smoothstep(values: np.ndarray) -> np.ndarray:
    values = np.clip(np.asarray(values, dtype=np.float64), 0.0, 1.0)
    return values**3 * (values * (values * 6.0 - 15.0) + 10.0)


def smooth_pose_transition(
    start: np.ndarray, stop: np.ndarray, frame_count: int
) -> np.ndarray:
    """Create a zero-slope, zero-acceleration quintic pose transition."""
    if frame_count < 2:
        raise ValueError("frame_count must be at least 2")
    start_array = np.asarray(start, dtype=np.float64)
    stop_array = np.asarray(stop, dtype=np.float64)
    if start_array.shape != stop_array.shape:
        raise ValueError("start and stop poses must have the same shape")
    weights = _quintic_smoothstep(np.linspace(0.0, 1.0, frame_count))[:, None]
    poses = start_array[None, :] + weights * (stop_array - start_array)[None, :]
    poses[0] = start_array
    poses[-1] = stop_array
    return poses


def build_timeline(
    model: mujoco.MjModel,
    recordings_dir: Path,
    *,
    specs: Sequence[MotionSpec] = DEFAULT_MOTION_SPECS,
    fps: int = 30,
    transition_seconds: float = 1.2,
) -> Timeline:
    """Build a deterministic kinematic timeline from named source recordings."""
    if fps <= 0:
        raise ValueError("fps must be positive")
    if not math.isfinite(transition_seconds) or transition_seconds <= 0.0:
        raise ValueError("transition_seconds must be finite and positive")
    if not specs:
        raise ValueError("At least one motion is required")

    directory = Path(recordings_dir).expanduser().resolve()
    bindings = resolve_joint_bindings(model)
    clips: list[tuple[MotionSpec, Recording, np.ndarray, float]] = []
    usages: list[RecordingUsage] = []
    for spec in specs:
        if not spec.name or Path(spec.name).name != spec.name:
            raise ValueError(f"Unsafe motion name: {spec.name!r}")
        recording = load_recording(directory / f"{spec.name}.csv")
        targets, included_duration = _resample_recording(
            recording, bindings, fps, spec.max_duration_seconds
        )
        clips.append((spec, recording, targets, included_duration))
        usages.append(
            RecordingUsage(
                name=recording.name,
                source_path=recording.source_path,
                row_count=recording.row_count,
                source_duration_seconds=recording.duration_seconds,
                included_duration_seconds=included_duration,
                normalized_min=tuple(
                    float(value) for value in recording.normalized_positions.min(axis=0)
                ),
                normalized_max=tuple(
                    float(value) for value in recording.normalized_positions.max(axis=0)
                ),
            )
        )

    chunks: list[np.ndarray] = []
    segments: list[TimelineSegment] = []
    cursor = 0
    for index, (spec, recording, clip, included_duration) in enumerate(clips):
        if index == 0:
            chunks.append(clip)
            start_frame = 0
            end_frame = clip.shape[0] - 1
            cursor = clip.shape[0]
        else:
            previous = clips[index - 1][2]
            transition_frame_count = max(2, int(round(transition_seconds * fps)) + 1)
            transition = smooth_pose_transition(
                previous[-1], clip[0], transition_frame_count
            )[1:]
            transition_start = cursor - 1
            chunks.append(transition)
            cursor += transition.shape[0]
            transition_end = cursor - 1
            segments.append(
                TimelineSegment(
                    kind="transition",
                    name=f"{clips[index - 1][0].name}_to_{spec.name}",
                    start_frame=transition_start,
                    end_frame=transition_end,
                    included_duration_seconds=transition_seconds,
                )
            )
            start_frame = cursor - 1
            clip_without_duplicate = clip[1:]
            chunks.append(clip_without_duplicate)
            cursor += clip_without_duplicate.shape[0]
            end_frame = cursor - 1

        segments.append(
            TimelineSegment(
                kind="motion",
                name=spec.name,
                start_frame=start_frame,
                end_frame=end_frame,
                source_duration_seconds=recording.duration_seconds,
                included_duration_seconds=included_duration,
            )
        )

    targets = np.vstack(chunks)
    if not np.isfinite(targets).all():
        raise ValueError("Timeline contains non-finite joint targets")
    for column, servo_name in enumerate(SERVO_TO_JOINT):
        binding = bindings[servo_name]
        if np.any(targets[:, column] < binding.lower) or np.any(
            targets[:, column] > binding.upper
        ):
            raise ValueError(f"Timeline target exceeds the joint range for {servo_name}")
    times = np.arange(targets.shape[0], dtype=np.float64) / fps
    return Timeline(
        fps=fps,
        times=times,
        targets=targets,
        segments=tuple(segments),
        recordings=tuple(usages),
    )


def _named_id(
    model: mujoco.MjModel, object_type: mujoco.mjtObj, name: str, description: str
) -> int:
    object_id = int(mujoco.mj_name2id(model, object_type, name))
    if object_id < 0:
        raise ValueError(f"{description} not found in model: {name}")
    return object_id


def capture_base_reference(
    model: mujoco.MjModel,
    data: mujoco.MjData,
    *,
    root_joint_name: str = ROOT_JOINT_NAME,
    base_body_name: str = BASE_BODY_NAME,
) -> BaseReference:
    """Capture the physical base transform at original qpos0/root identity."""
    root_joint_id = _named_id(
        model, mujoco.mjtObj.mjOBJ_JOINT, root_joint_name, "root freejoint"
    )
    if int(model.jnt_type[root_joint_id]) != int(mujoco.mjtJoint.mjJNT_FREE):
        raise ValueError(f"Root joint is not a freejoint: {root_joint_name}")
    root_qpos_address = int(model.jnt_qposadr[root_joint_id])
    base_body_id = _named_id(
        model, mujoco.mjtObj.mjOBJ_BODY, base_body_name, "physical base body"
    )

    mujoco.mj_resetData(model, data)
    data.qpos[:] = model.qpos0
    data.qpos[root_qpos_address : root_qpos_address + 7] = (
        0.0,
        0.0,
        0.0,
        1.0,
        0.0,
        0.0,
        0.0,
    )
    data.qvel[:] = 0.0
    data.time = 0.0
    mujoco.mj_forward(model, data)
    return BaseReference(
        position=data.xpos[base_body_id].copy(),
        rotation=data.xmat[base_body_id].reshape(3, 3).copy(),
        root_joint_id=root_joint_id,
        root_qpos_address=root_qpos_address,
        base_body_id=base_body_id,
        root_joint_name=root_joint_name,
        base_body_name=base_body_name,
    )


def _rotation_error_radians(reference: np.ndarray, actual: np.ndarray) -> float:
    relative = reference.T @ actual
    sine = 0.5 * np.linalg.norm(
        np.array(
            [
                relative[2, 1] - relative[1, 2],
                relative[0, 2] - relative[2, 0],
                relative[1, 0] - relative[0, 1],
            ]
        )
    )
    cosine = float(np.clip((np.trace(relative) - 1.0) / 2.0, -1.0, 1.0))
    return float(math.atan2(sine, cosine))


def apply_anchored_pose(
    model: mujoco.MjModel,
    data: mujoco.MjData,
    bindings: Mapping[str, JointBinding],
    targets: Mapping[str, float] | Sequence[float] | np.ndarray,
    reference: BaseReference,
    *,
    simulation_time: float,
) -> AnchorDiagnostics:
    """Apply one pose, solving root = T_ref * inverse(T_raw)."""
    if isinstance(targets, Mapping):
        target_values = np.asarray([targets[name] for name in SERVO_TO_JOINT], dtype=np.float64)
    else:
        target_values = np.asarray(targets, dtype=np.float64)
    if target_values.shape != (len(SERVO_TO_JOINT),):
        raise ValueError(f"Expected {len(SERVO_TO_JOINT)} joint targets")
    if not np.isfinite(target_values).all() or not math.isfinite(simulation_time):
        raise ValueError("Pose targets and simulation time must be finite")

    root_qpos = reference.root_qpos_address
    data.qpos[root_qpos : root_qpos + 7] = (0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0)
    data.qvel[:] = 0.0
    for column, servo_name in enumerate(SERVO_TO_JOINT):
        binding = bindings[servo_name]
        value = float(target_values[column])
        tolerance = 1e-12
        if value < binding.lower - tolerance or value > binding.upper + tolerance:
            raise ValueError(
                f"Target {value} for {servo_name} lies outside "
                f"[{binding.lower}, {binding.upper}]"
            )
        data.qpos[binding.qpos_address] = float(
            np.clip(value, binding.lower, binding.upper)
        )
    data.time = float(simulation_time)

    # With the root identity, T_raw is the physical base pose induced by the
    # serial chain.  Premultiplying the whole root by T_ref @ inv(T_raw) keeps
    # that downstream base at its verified qpos0 world transform.
    mujoco.mj_forward(model, data)
    raw_position = data.xpos[reference.base_body_id].copy()
    raw_rotation = data.xmat[reference.base_body_id].reshape(3, 3).copy()
    root_rotation = reference.rotation @ raw_rotation.T
    root_position = reference.position - root_rotation @ raw_position
    root_quaternion = np.empty(4, dtype=np.float64)
    mujoco.mju_mat2Quat(root_quaternion, np.ascontiguousarray(root_rotation.reshape(9)))
    root_quaternion /= np.linalg.norm(root_quaternion)

    data.qpos[root_qpos : root_qpos + 3] = root_position
    data.qpos[root_qpos + 3 : root_qpos + 7] = root_quaternion
    mujoco.mj_forward(model, data)
    actual_position = data.xpos[reference.base_body_id]
    actual_rotation = data.xmat[reference.base_body_id].reshape(3, 3)
    return AnchorDiagnostics(
        translation_drift_m=float(np.linalg.norm(actual_position - reference.position)),
        orientation_drift_rad=_rotation_error_radians(reference.rotation, actual_rotation),
    )


def mesh_world_aabb(
    model: mujoco.MjModel,
    data: mujoco.MjData,
    mesh_name: str,
    *,
    geom_group: int = 2,
) -> tuple[np.ndarray, np.ndarray]:
    """Return the exact vertex AABB for one named mesh geom in world space."""
    mesh_id = _named_id(model, mujoco.mjtObj.mjOBJ_MESH, mesh_name, "mesh")
    geom_ids = [
        geom_id
        for geom_id in range(model.ngeom)
        if int(model.geom_type[geom_id]) == int(mujoco.mjtGeom.mjGEOM_MESH)
        and int(model.geom_dataid[geom_id]) == mesh_id
        and int(model.geom_group[geom_id]) == geom_group
    ]
    if len(geom_ids) != 1:
        raise ValueError(
            f"Expected one group-{geom_group} geom for mesh {mesh_name}, found {geom_ids}"
        )
    vertex_address = int(model.mesh_vertadr[mesh_id])
    vertex_count = int(model.mesh_vertnum[mesh_id])
    vertices = model.mesh_vert[vertex_address : vertex_address + vertex_count]
    geom_id = geom_ids[0]
    rotation = data.geom_xmat[geom_id].reshape(3, 3)
    world_vertices = vertices @ rotation.T + data.geom_xpos[geom_id]
    return world_vertices.min(axis=0), world_vertices.max(axis=0)


def _visual_world_aabb(
    model: mujoco.MjModel, data: mujoco.MjData
) -> tuple[np.ndarray, np.ndarray]:
    lower_parts: list[np.ndarray] = []
    upper_parts: list[np.ndarray] = []
    for geom_id in range(model.ngeom):
        if int(model.geom_group[geom_id]) != 2 or float(model.geom_rgba[geom_id, 3]) <= 0.0:
            continue
        geom_type = int(model.geom_type[geom_id])
        if geom_type == int(mujoco.mjtGeom.mjGEOM_PLANE):
            continue
        if geom_type == int(mujoco.mjtGeom.mjGEOM_MESH):
            mesh_id = int(model.geom_dataid[geom_id])
            vertex_address = int(model.mesh_vertadr[mesh_id])
            vertex_count = int(model.mesh_vertnum[mesh_id])
            vertices = model.mesh_vert[vertex_address : vertex_address + vertex_count]
            rotation = data.geom_xmat[geom_id].reshape(3, 3)
            world_vertices = vertices @ rotation.T + data.geom_xpos[geom_id]
            lower_parts.append(world_vertices.min(axis=0))
            upper_parts.append(world_vertices.max(axis=0))
        else:
            radius = float(model.geom_rbound[geom_id])
            if math.isfinite(radius) and radius > 0.0:
                lower_parts.append(data.geom_xpos[geom_id] - radius)
                upper_parts.append(data.geom_xpos[geom_id] + radius)
    if not lower_parts:
        raise ValueError("Model has no visible group-2 geometry")
    return np.min(lower_parts, axis=0), np.max(upper_parts, axis=0)


def analyze_timeline_geometry(
    model: mujoco.MjModel,
    data: mujoco.MjData,
    bindings: Mapping[str, JointBinding],
    timeline: Timeline,
    reference: BaseReference,
) -> GeometryAnalysis:
    visible_lowers: list[np.ndarray] = []
    visible_uppers: list[np.ndarray] = []
    maximum_translation_drift = 0.0
    maximum_orientation_drift = 0.0
    base_lower: np.ndarray | None = None
    base_upper: np.ndarray | None = None

    for frame_index, targets in enumerate(timeline.targets):
        diagnostics = apply_anchored_pose(
            model,
            data,
            bindings,
            targets,
            reference,
            simulation_time=float(timeline.times[frame_index]),
        )
        maximum_translation_drift = max(
            maximum_translation_drift, diagnostics.translation_drift_m
        )
        maximum_orientation_drift = max(
            maximum_orientation_drift, diagnostics.orientation_drift_rad
        )
        lower, upper = _visual_world_aabb(model, data)
        visible_lowers.append(lower)
        visible_uppers.append(upper)
        if base_lower is None:
            base_lower, base_upper = mesh_world_aabb(
                model, data, BASE_MESH_NAME, geom_group=2
            )

    assert base_lower is not None and base_upper is not None
    return GeometryAnalysis(
        visible_lower=np.min(visible_lowers, axis=0),
        visible_upper=np.max(visible_uppers, axis=0),
        base_mesh_lower=base_lower,
        base_mesh_upper=base_upper,
        maximum_translation_drift_m=maximum_translation_drift,
        maximum_orientation_drift_rad=maximum_orientation_drift,
    )


def configure_render_appearance(model: mujoco.MjModel) -> None:
    """Apply studio appearance in memory without editing source MJCF/assets."""
    floor_id = int(mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "floor"))
    if floor_id >= 0:
        model.geom_matid[floor_id] = -1
        model.geom_rgba[floor_id] = (0.72, 0.74, 0.77, 1.0)

    if hasattr(model, "mat_emission"):
        model.mat_emission[:] = 0.0
    model.vis.headlight.ambient[:] = (0.32, 0.32, 0.32)
    model.vis.headlight.diffuse[:] = (0.55, 0.55, 0.55)
    model.vis.headlight.specular[:] = (0.08, 0.08, 0.08)
    for light_id in range(model.nlight):
        model.light_ambient[light_id] = (0.18, 0.18, 0.18)
        model.light_diffuse[light_id] = (0.72, 0.72, 0.72)
        model.light_specular[light_id] = (0.12, 0.12, 0.12)
        model.light_dir[light_id] = (0.30, -0.25, -1.0)

    # Replace only the compiled in-memory skybox pixels with a neutral gradient.
    for texture_id in range(model.ntex):
        if int(model.tex_type[texture_id]) != int(mujoco.mjtTexture.mjTEXTURE_SKYBOX):
            continue
        width = int(model.tex_width[texture_id])
        height = int(model.tex_height[texture_id])
        address = int(model.tex_adr[texture_id])
        size = width * height * 3
        texture = model.tex_data[address : address + size].reshape(height, width, 3)
        top = np.array([235.0, 239.0, 244.0])
        bottom = np.array([194.0, 203.0, 214.0])
        weights = np.linspace(0.0, 1.0, height, dtype=np.float64)[:, None]
        rows = np.rint(top[None, :] * (1.0 - weights) + bottom[None, :] * weights).astype(
            np.uint8
        )
        texture[:] = rows[:, None, :]


def camera_for_geometry(
    analysis: GeometryAnalysis,
    *,
    azimuth: float,
    elevation: float,
    aspect_ratio: float,
) -> CameraConfiguration:
    if not all(math.isfinite(value) for value in (azimuth, elevation, aspect_ratio)):
        raise ValueError("Camera values must be finite")
    if aspect_ratio <= 0.0:
        raise ValueError("Camera aspect ratio must be positive")
    span = analysis.visible_upper - analysis.visible_lower
    center = (analysis.visible_lower + analysis.visible_upper) / 2.0
    center[2] += 0.01
    framing_span = max(float(span[2]), float(max(span[0], span[1]) / aspect_ratio))
    distance = max(0.75, framing_span * 2.05, float(max(span)) * 1.75)
    return CameraConfiguration(
        lookat=center,
        distance=distance,
        azimuth=float(azimuth),
        elevation=float(elevation),
    )


def _mujoco_camera(configuration: CameraConfiguration) -> mujoco.MjvCamera:
    camera = mujoco.MjvCamera()
    camera.type = mujoco.mjtCamera.mjCAMERA_FREE
    camera.lookat[:] = configuration.lookat
    camera.distance = configuration.distance
    camera.azimuth = configuration.azimuth
    camera.elevation = configuration.elevation
    return camera


def _scene_option() -> mujoco.MjvOption:
    option = mujoco.MjvOption()
    option.geomgroup[3] = 0
    option.sitegroup[3] = 0
    return option


def _representative_samples(timeline: Timeline) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    ranges = np.ptp(timeline.targets, axis=0)
    ranges[ranges < 1e-12] = 1.0
    for segment in timeline.segments:
        if segment.kind != "motion":
            continue
        segment_targets = timeline.targets[segment.start_frame : segment.end_frame + 1]
        baseline = segment_targets[0]
        distances = np.linalg.norm((segment_targets - baseline) / ranges, axis=1)
        local_index = int(np.argmax(distances))
        frame_index = segment.start_frame + local_index
        samples.append(
            {
                "motion": segment.name,
                "frame_index": frame_index,
                "simulation_time_seconds": float(timeline.times[frame_index]),
                "selection": "largest normalized displacement from motion start",
            }
        )
    return samples


def render_preview_montage(
    model: mujoco.MjModel,
    data: mujoco.MjData,
    bindings: Mapping[str, JointBinding],
    timeline: Timeline,
    reference: BaseReference,
    camera_configuration: CameraConfiguration,
    output_path: Path,
    *,
    tile_width: int,
    tile_height: int,
) -> list[dict[str, Any]]:
    samples = _representative_samples(timeline)
    columns = 3
    rows = int(math.ceil(len(samples) / columns))
    gap = 6
    montage = Image.new(
        "RGB",
        (
            columns * tile_width + (columns - 1) * gap,
            rows * tile_height + (rows - 1) * gap,
        ),
        (205, 212, 221),
    )
    model.vis.global_.offwidth = max(int(model.vis.global_.offwidth), tile_width)
    model.vis.global_.offheight = max(int(model.vis.global_.offheight), tile_height)
    renderer = mujoco.Renderer(model, width=tile_width, height=tile_height)
    camera = _mujoco_camera(camera_configuration)
    option = _scene_option()
    try:
        for sample_index, sample in enumerate(samples):
            frame_index = int(sample["frame_index"])
            diagnostics = apply_anchored_pose(
                model,
                data,
                bindings,
                timeline.targets[frame_index],
                reference,
                simulation_time=float(timeline.times[frame_index]),
            )
            renderer.update_scene(data, camera=camera, scene_option=option)
            frame = renderer.render().copy()
            x = (sample_index % columns) * (tile_width + gap)
            y = (sample_index // columns) * (tile_height + gap)
            montage.paste(Image.fromarray(frame, mode="RGB"), (x, y))
            sample["translation_drift_m"] = diagnostics.translation_drift_m
            sample["orientation_drift_rad"] = diagnostics.orientation_drift_rad
    finally:
        renderer.close()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    montage.save(output_path, format="PNG", compress_level=6)
    return samples


def _ffmpeg_command(
    ffmpeg_path: str,
    temporary_path: Path,
    *,
    width: int,
    height: int,
    fps: int,
) -> list[str]:
    return [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "rawvideo",
        "-pixel_format",
        "rgb24",
        "-video_size",
        f"{width}x{height}",
        "-framerate",
        str(fps),
        "-i",
        "pipe:0",
        "-map",
        "0:v:0",
        "-an",
        "-sn",
        "-dn",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(fps),
        "-movflags",
        "+faststart",
        "-f",
        "mp4",
        str(temporary_path),
    ]


def render_video(
    model: mujoco.MjModel,
    data: mujoco.MjData,
    bindings: Mapping[str, JointBinding],
    timeline: Timeline,
    reference: BaseReference,
    camera_configuration: CameraConfiguration,
    output_path: Path,
    *,
    width: int,
    height: int,
    ffmpeg_path: str,
) -> dict[str, Any]:
    """Render RGB frames one at a time into a streaming FFmpeg pipe."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.stem}.rendering-",
        suffix=".mp4",
        dir=output_path.parent,
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    command = _ffmpeg_command(
        ffmpeg_path,
        temporary_path,
        width=width,
        height=height,
        fps=timeline.fps,
    )

    model.vis.global_.offwidth = max(int(model.vis.global_.offwidth), width)
    model.vis.global_.offheight = max(int(model.vis.global_.offheight), height)
    renderer = mujoco.Renderer(model, width=width, height=height)
    camera = _mujoco_camera(camera_configuration)
    option = _scene_option()
    process: subprocess.Popen[bytes] | None = None
    stderr_text = ""
    maximum_translation_drift = 0.0
    maximum_orientation_drift = 0.0
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        if process.stdin is None or process.stderr is None:
            raise RuntimeError("Could not open FFmpeg pipes")
        progress_interval = max(timeline.fps * 5, 1)
        for frame_index, targets in enumerate(timeline.targets):
            diagnostics = apply_anchored_pose(
                model,
                data,
                bindings,
                targets,
                reference,
                simulation_time=float(timeline.times[frame_index]),
            )
            maximum_translation_drift = max(
                maximum_translation_drift, diagnostics.translation_drift_m
            )
            maximum_orientation_drift = max(
                maximum_orientation_drift, diagnostics.orientation_drift_rad
            )
            renderer.update_scene(data, camera=camera, scene_option=option)
            frame = np.ascontiguousarray(renderer.render(), dtype=np.uint8)
            try:
                process.stdin.write(frame.tobytes())
            except BrokenPipeError as exc:
                stderr_text = process.stderr.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"FFmpeg pipe closed early: {stderr_text.strip()}") from exc
            if (frame_index + 1) % progress_interval == 0 or (
                frame_index + 1 == timeline.frame_count
            ):
                print(
                    f"Rendered {frame_index + 1}/{timeline.frame_count} frames "
                    f"({(frame_index + 1) / timeline.fps:.1f}s)",
                    flush=True,
                )
        process.stdin.close()
        stderr_text = process.stderr.read().decode("utf-8", errors="replace")
        return_code = process.wait()
        if return_code != 0:
            raise RuntimeError(
                f"FFmpeg failed with exit code {return_code}: {stderr_text.strip()}"
            )
        os.replace(temporary_path, output_path)
    except BaseException:
        if process is not None and process.poll() is None:
            if process.stdin is not None and not process.stdin.closed:
                process.stdin.close()
            process.kill()
            process.wait()
        temporary_path.unlink(missing_ok=True)
        raise
    finally:
        renderer.close()

    return {
        "ffmpeg_command": shlex.join(command[:-1] + [str(output_path)]),
        "maximum_translation_drift_m": maximum_translation_drift,
        "maximum_orientation_drift_rad": maximum_orientation_drift,
    }


def probe_video(ffprobe_path: str, path: Path, *, expected_fps: int) -> dict[str, Any]:
    command = [
        ffprobe_path,
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFprobe failed: {result.stderr.strip()}")
    payload = json.loads(result.stdout)
    streams = payload.get("streams", [])
    video_streams = [stream for stream in streams if stream.get("codec_type") == "video"]
    if len(video_streams) != 1 or len(streams) != 1:
        raise RuntimeError(f"Expected exactly one video-only stream, got {streams}")
    stream = video_streams[0]
    frame_rate_text = stream.get("avg_frame_rate") or stream.get("r_frame_rate")
    numerator, denominator = (int(value) for value in frame_rate_text.split("/"))
    frame_rate = numerator / denominator
    if not math.isclose(frame_rate, expected_fps, rel_tol=0.0, abs_tol=1e-9):
        raise RuntimeError(f"Unexpected encoded frame rate: {frame_rate_text}")
    return {
        "probe_command": shlex.join(command),
        "codec_name": stream.get("codec_name"),
        "profile": stream.get("profile"),
        "pixel_format": stream.get("pix_fmt"),
        "width": int(stream["width"]),
        "height": int(stream["height"]),
        "r_frame_rate": stream.get("r_frame_rate"),
        "avg_frame_rate": stream.get("avg_frame_rate"),
        "frame_count": int(stream["nb_frames"]) if stream.get("nb_frames") else None,
        "duration_seconds": float(stream.get("duration", payload["format"]["duration"])),
        "stream_types": [stream_item.get("codec_type") for stream_item in streams],
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _resolve_asset_file(
    xml_path: Path, root: ET.Element, tag: str, file_value: str
) -> Path:
    compiler = root.find("compiler")
    compiler_values = compiler.attrib if compiler is not None else {}
    asset_directory = compiler_values.get("assetdir", "")
    if tag == "mesh":
        asset_directory = compiler_values.get("meshdir", asset_directory)
    elif tag == "texture":
        asset_directory = compiler_values.get("texturedir", asset_directory)
    return (xml_path.parent / asset_directory / file_value).resolve()


def collect_source_files(
    model_path: Path, recording_paths: Iterable[Path]
) -> dict[Path, str]:
    """Collect MJCF includes/assets, recordings, and normalization evidence."""
    sources: dict[Path, str] = {}
    pending = [Path(model_path).resolve()]
    while pending:
        xml_path = pending.pop()
        if xml_path in sources:
            continue
        _require_file(xml_path, "MJCF source")
        sources[xml_path] = "mjcf"
        root = ET.parse(xml_path).getroot()
        for include in root.findall(".//include"):
            include_value = include.get("file")
            if include_value:
                pending.append((xml_path.parent / include_value).resolve())
        for tag in ("mesh", "texture", "hfield", "skin"):
            for element in root.findall(f".//{tag}"):
                file_value = element.get("file")
                if not file_value:
                    continue
                asset_path = _resolve_asset_file(xml_path, root, tag, file_value)
                _require_file(asset_path, f"MJCF {tag} asset")
                sources[asset_path] = f"mjcf_{tag}_asset"
    for recording_path in recording_paths:
        resolved = _require_file(Path(recording_path), "recording source")
        sources[resolved] = "recording"
    for evidence_path in NORMALIZATION_EVIDENCE_PATHS:
        resolved = _require_file(evidence_path, "normalization evidence")
        sources[resolved] = "normalization_evidence"
    return sources


def _source_hashes(sources: Mapping[Path, str]) -> dict[Path, str]:
    return {path: sha256_file(path) for path in sources}


def _assert_source_hashes_unchanged(before: Mapping[Path, str]) -> None:
    after = _source_hashes({path: "" for path in before})
    changed = [path for path, digest in before.items() if after[path] != digest]
    if changed:
        raise RuntimeError(f"Protected sources changed during export: {changed}")


def _jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return _jsonable(dataclasses.asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def _current_command() -> str:
    return shlex.join([sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]])


def _load_previous_metadata(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def build_metadata(
    *,
    mode: str,
    model_path: Path,
    model: mujoco.MjModel,
    bindings: Mapping[str, JointBinding],
    timeline: Timeline,
    reference: BaseReference,
    analysis: GeometryAnalysis,
    camera_configuration: CameraConfiguration,
    sources: Mapping[Path, str],
    source_hashes: Mapping[Path, str],
    preview_path: Path,
    preview_samples: Sequence[Mapping[str, Any]],
    preview_dimensions: tuple[int, int] | None,
    output_path: Path,
    final_probe: Mapping[str, Any] | None,
    render_diagnostics: Mapping[str, Any] | None,
    previous_metadata: Mapping[str, Any],
) -> dict[str, Any]:
    commands = dict(previous_metadata.get("commands", {}))
    commands["preview" if mode == "preview_only" else "final"] = _current_command()
    actuator_order = [
        mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_ACTUATOR, index)
        for index in range(model.nu)
    ]
    joint_mapping = {
        servo_name: {
            "csv_column": f"{servo_name}.pos",
            "joint_name": binding.joint_name,
            "joint_id": binding.joint_id,
            "qpos_address": binding.qpos_address,
            "actuator_id": binding.actuator_id,
            "joint_range_radians": [binding.lower, binding.upper],
        }
        for servo_name, binding in bindings.items()
    }
    preview_info: dict[str, Any] | None = None
    if preview_path.is_file():
        with Image.open(preview_path) as preview_image:
            actual_dimensions = list(preview_image.size)
        preview_info = {
            "path": str(preview_path),
            "sha256": sha256_file(preview_path),
            "dimensions": actual_dimensions,
            "tile_dimensions": list(preview_dimensions) if preview_dimensions else None,
            "contains_rendered_text": False,
            "samples": list(preview_samples),
        }

    return {
        "schema_version": 1,
        "artifact_status": mode,
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "commands": commands,
        "truthful_scope": {
            "render_type": "MuJoCo forward-kinematic animation",
            "dynamics_validated": False,
            "physical_motor_execution": False,
            "vision_recognition": False,
            "openai_decision_making": False,
            "hardware_or_api_calls": False,
            "audio": False,
            "on_screen_text": False,
        },
        "sources": [
            {
                "path": str(path),
                "role": sources[path],
                "sha256": source_hashes[path],
            }
            for path in sorted(sources, key=lambda item: str(item))
        ],
        "model": {
            "path": str(model_path),
            "mujoco_version": mujoco.__version__,
            "nq": int(model.nq),
            "nv": int(model.nv),
            "joint_count": int(model.njnt),
            "actuator_count": int(model.nu),
            "actuator_array_order": actuator_order,
            "joint_mapping": joint_mapping,
            "geometry_source_edited": False,
        },
        "normalization": {
            "source_convention": "LeRobot RANGE_M100_100",
            "evidence": "original leader/follower configs set use_degrees=False",
            "conversion": "clip to [-100,100], then linearly map -100 to MJCF lower and +100 to MJCF upper",
            "calibration_status": "unverified simulation convention; not real servo calibration",
        },
        "animation": {
            "servo_order": list(SERVO_TO_JOINT),
            "fps": timeline.fps,
            "frame_count": timeline.frame_count,
            "final_simulation_time_seconds": timeline.duration_seconds,
            "nominal_encoded_duration_seconds": timeline.encoded_duration_seconds,
            "motions": [_jsonable(usage) for usage in timeline.recordings],
            "segments": [_jsonable(segment) for segment in timeline.segments],
            "source_smoothing": "0.18 s centered Hann filter with source endpoints preserved",
            "transition_smoothing": "quintic smoothstep with exact zero-slope endpoints",
        },
        "anchoring": {
            "root_joint": reference.root_joint_name,
            "physical_base_body": reference.base_body_name,
            "physical_base_mesh": BASE_MESH_NAME,
            "solve_per_frame": "root transform = T_ref * inverse(T_raw), then mj_forward",
            "reference_source": "original qpos0 with root freejoint identity",
            "reference_position_m": reference.position.tolist(),
            "reference_rotation_matrix": reference.rotation.tolist(),
            "reference_base_mesh_world_aabb_m": {
                "lower": analysis.base_mesh_lower.tolist(),
                "upper": analysis.base_mesh_upper.tolist(),
            },
            "maximum_measured_translation_drift_m": analysis.maximum_translation_drift_m,
            "maximum_measured_orientation_drift_rad": analysis.maximum_orientation_drift_rad,
        },
        "composition": {
            "camera": _jsonable(camera_configuration),
            "whole_motion_visible_aabb_m": {
                "lower": analysis.visible_lower.tolist(),
                "upper": analysis.visible_upper.tolist(),
            },
            "collision_geom_group_3_visible": False,
            "ground": "solid studio ground configured in memory only",
            "background": "neutral gradient skybox configured in memory only",
            "material_emission": 0.0,
        },
        "preview": preview_info,
        "final_video": (
            {
                "path": str(output_path),
                **dict(final_probe or {}),
                "render_diagnostics": dict(render_diagnostics or {}),
                "contains_audio_subtitle_or_data_streams": False,
                "contains_rendered_text": False,
            }
            if final_probe is not None
            else None
        ),
        "limitations": [
            "Kinematic replay only; no torque, contact, stability, or dynamics claim.",
            "RANGE_M100_100 to MJCF-limit mapping is not a measured real-servo calibration.",
            "No real hardware, camera, microphone, serial bus, network service, or API was used.",
            "Visual review is still required for identity, composition, clipping, and intersections.",
        ],
    }


def _readme_text(metadata: Mapping[str, Any]) -> str:
    preview = metadata.get("preview")
    final_video = metadata.get("final_video")
    commands = metadata.get("commands", {})
    lines = [
        "# LeLamp MuJoCo Preview (No Text)",
        "",
        "This folder contains a silent, no-on-screen-text trial rendered from the original LeLamp MJCF geometry and original LeLamp CSV recordings.",
        "",
        "## Artifacts",
        "",
    ]
    if preview:
        lines.append(f"- Preview montage: `{preview['path']}`")
    if final_video:
        lines.append(f"- Final video: `{final_video['path']}`")
    else:
        lines.append("- Final video: not rendered in preview-only mode")
    lines.extend(
        [
            "- Machine-readable provenance and QA: `preview_metadata.json`",
            "",
            "## Source and motion convention",
            "",
            f"- Original scene: `{metadata['model']['path']}`",
            "- Motion columns are resolved by servo/joint name; the MJCF actuator array order is not assumed.",
            "- CSV values are treated as LeRobot `RANGE_M100_100` because the original leader/follower configs use `use_degrees=False`.",
            "- Mapping normalized endpoints to MJCF limits is an unverified simulation convention, not a real calibration.",
            "",
            "## Anchoring",
            "",
            "At every frame, the root freejoint is first set to identity, the raw physical-base transform is measured, and the root transform is solved as `T_ref * inverse(T_raw)`. `T_ref` is the original qpos0 transform of `scs215_v5`; no base orientation was guessed. The physical `lamp_base` mesh remains at its original ground-touching reference.",
            "",
            "## Truthful limitations",
            "",
            "This is MuJoCo forward-kinematic animation. It does not validate dynamics, torque, contacts, physical motor execution, vision recognition, or OpenAI decision-making. No hardware, external API, or network service was called.",
            "",
            "## Exact exporter commands",
            "",
        ]
    )
    if commands.get("preview"):
        lines.extend(["Preview:", "", f"```sh\n{commands['preview']}\n```", ""])
    if commands.get("final"):
        lines.extend(["Final:", "", f"```sh\n{commands['final']}\n```", ""])
    return "\n".join(lines).rstrip() + "\n"


def _ensure_writable_outputs(paths: Iterable[Path], *, overwrite: bool) -> None:
    existing = [path for path in paths if path.exists()]
    if existing and not overwrite:
        formatted = "\n".join(f"  - {path}" for path in existing)
        raise FileExistsError(
            "Refusing to overwrite existing deliverables without --overwrite:\n" + formatted
        )


def _write_metadata_and_readme(
    metadata: Mapping[str, Any],
    metadata_path: Path,
    readme_path: Path,
) -> None:
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    readme_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(
        json.dumps(_jsonable(metadata), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    readme_path.write_text(_readme_text(metadata), encoding="utf-8")


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render the original LeLamp as a silent, no-text kinematic preview/video."
    )
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL_PATH)
    parser.add_argument("--recordings-dir", type=Path, default=DEFAULT_RECORDINGS_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--preview-output", type=Path)
    parser.add_argument("--metadata-output", type=Path)
    parser.add_argument("--readme-output", type=Path)
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--preview-width", type=int, default=512)
    parser.add_argument("--preview-height", type=int, default=288)
    parser.add_argument("--camera-azimuth", type=float, default=145.0)
    parser.add_argument("--camera-elevation", type=float, default=-18.0)
    parser.add_argument("--transition-seconds", type=float, default=1.2)
    parser.add_argument(
        "--motions",
        nargs="+",
        default=[spec.name for spec in DEFAULT_MOTION_SPECS],
        choices=[spec.name for spec in DEFAULT_MOTION_SPECS],
    )
    parser.add_argument(
        "--preview-only",
        action="store_true",
        help="Render only the small no-text contact sheet and metadata.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Explicitly allow replacement of existing requested deliverables.",
    )
    return parser.parse_args(argv)


def _validate_dimensions(width: int, height: int, description: str) -> None:
    if not (64 <= width <= 4096 and 64 <= height <= 4096):
        raise ValueError(f"{description} dimensions must each be between 64 and 4096")
    if width % 2 or height % 2:
        raise ValueError(f"{description} dimensions must be even")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    _validate_dimensions(args.width, args.height, "video")
    _validate_dimensions(args.preview_width, args.preview_height, "preview tile")
    if args.fps <= 0:
        raise ValueError("fps must be positive")

    model_path = _require_file(args.model, "model")
    recordings_dir = args.recordings_dir.expanduser().resolve()
    output_path = args.output.expanduser().resolve()
    output_directory = output_path.parent
    preview_path = (
        args.preview_output.expanduser().resolve()
        if args.preview_output
        else output_directory / DEFAULT_PREVIEW_NAME
    )
    metadata_path = (
        args.metadata_output.expanduser().resolve()
        if args.metadata_output
        else output_directory / DEFAULT_METADATA_NAME
    )
    readme_path = (
        args.readme_output.expanduser().resolve()
        if args.readme_output
        else output_directory / DEFAULT_README_NAME
    )
    requested_outputs = (
        (preview_path, metadata_path, readme_path)
        if args.preview_only
        else (output_path, metadata_path, readme_path)
    )
    _ensure_writable_outputs(requested_outputs, overwrite=args.overwrite)

    spec_defaults = {spec.name: spec for spec in DEFAULT_MOTION_SPECS}
    specs = tuple(spec_defaults[name] for name in args.motions)
    recording_paths = [recordings_dir / f"{spec.name}.csv" for spec in specs]
    sources = collect_source_files(model_path, recording_paths)
    source_hashes_before = _source_hashes(sources)

    model = mujoco.MjModel.from_xml_path(str(model_path))
    data = mujoco.MjData(model)
    bindings = resolve_joint_bindings(model)
    timeline = build_timeline(
        model,
        recordings_dir,
        specs=specs,
        fps=args.fps,
        transition_seconds=args.transition_seconds,
    )
    reference = capture_base_reference(model, data)
    analysis = analyze_timeline_geometry(model, data, bindings, timeline, reference)
    camera_configuration = camera_for_geometry(
        analysis,
        azimuth=args.camera_azimuth,
        elevation=args.camera_elevation,
        aspect_ratio=args.width / args.height,
    )
    configure_render_appearance(model)

    previous_metadata = _load_previous_metadata(metadata_path)
    preview_samples = _representative_samples(timeline)
    final_probe: dict[str, Any] | None = None
    render_diagnostics: dict[str, Any] | None = None
    if args.preview_only:
        preview_samples = render_preview_montage(
            model,
            data,
            bindings,
            timeline,
            reference,
            camera_configuration,
            preview_path,
            tile_width=args.preview_width,
            tile_height=args.preview_height,
        )
        print(f"Preview montage: {preview_path}", flush=True)
    else:
        ffmpeg_path = shutil.which("ffmpeg")
        ffprobe_path = shutil.which("ffprobe")
        if not ffmpeg_path or not ffprobe_path:
            raise RuntimeError("Both ffmpeg and ffprobe must be available on PATH")
        render_diagnostics = render_video(
            model,
            data,
            bindings,
            timeline,
            reference,
            camera_configuration,
            output_path,
            width=args.width,
            height=args.height,
            ffmpeg_path=ffmpeg_path,
        )
        final_probe = probe_video(ffprobe_path, output_path, expected_fps=args.fps)
        expected = {
            "codec_name": "h264",
            "pixel_format": "yuv420p",
            "width": args.width,
            "height": args.height,
            "frame_count": timeline.frame_count,
            "stream_types": ["video"],
        }
        mismatches = {
            key: (final_probe.get(key), expected_value)
            for key, expected_value in expected.items()
            if final_probe.get(key) != expected_value
        }
        if mismatches:
            raise RuntimeError(f"Encoded video verification mismatch: {mismatches}")
        print(f"Final video: {output_path}", flush=True)

    _assert_source_hashes_unchanged(source_hashes_before)
    metadata = build_metadata(
        mode="preview_only" if args.preview_only else "final",
        model_path=model_path,
        model=model,
        bindings=bindings,
        timeline=timeline,
        reference=reference,
        analysis=analysis,
        camera_configuration=camera_configuration,
        sources=sources,
        source_hashes=source_hashes_before,
        preview_path=preview_path,
        preview_samples=preview_samples,
        preview_dimensions=(args.preview_width, args.preview_height),
        output_path=output_path,
        final_probe=final_probe,
        render_diagnostics=render_diagnostics,
        previous_metadata=previous_metadata,
    )
    _write_metadata_and_readme(metadata, metadata_path, readme_path)
    print(f"Metadata: {metadata_path}", flush=True)
    print(f"README: {readme_path}", flush=True)
    print(
        f"Timeline: {timeline.frame_count} frames, "
        f"{timeline.encoded_duration_seconds:.3f}s nominal at {timeline.fps} fps",
        flush=True,
    )
    print(
        "Maximum base drift: "
        f"{analysis.maximum_translation_drift_m:.3e} m, "
        f"{analysis.maximum_orientation_drift_rad:.3e} rad",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
