"""Independent analytical reference and numerical differentiation; never a solver substitute."""

from __future__ import annotations

import math

from .contract import MechanismError, number


def rpm_to_rad_s(rpm: object) -> float:
    omega = number(rpm, "rpm", positive=True) * (2 * math.pi / 60)
    if not math.isfinite(omega) or omega <= 0:
        raise MechanismError("INVALID_NUMBER", "rpm is outside the supported finite range")
    return omega


def deg_to_rad(deg: object) -> float:
    return math.radians(number(deg, "deg"))


def duration_s(rpm: object, cycles: object) -> float:
    duration = number(cycles, "cycles", positive=True) * 2 * math.pi / rpm_to_rad_s(rpm)
    if not math.isfinite(duration) or duration <= 0:
        raise MechanismError("INVALID_NUMBER", "cycles and rpm produce an invalid duration")
    return duration


def slider_x_mm(radius_mm: object, rod_mm: object, angle_rad: object) -> float:
    r = number(radius_mm, "radius_mm", positive=True)
    l = number(rod_mm, "rod_mm", positive=True)
    angle = number(angle_rad, "angle_rad")
    if l <= r:
        raise MechanismError("UNSUPPORTED_GEOMETRY", "rod length must exceed crank radius")
    position = r * math.cos(angle) + l * math.sqrt(1 - (r / l * math.sin(angle)) ** 2)
    if not math.isfinite(position):
        raise MechanismError("INVALID_NUMBER", "dimensions produce a nonfinite slider position")
    return position


def rod_angle_rad(radius_mm: object, rod_mm: object, angle_rad: object) -> float:
    r = number(radius_mm, "radius_mm", positive=True)
    l = number(rod_mm, "rod_mm", positive=True)
    angle = number(angle_rad, "angle_rad")
    if l <= r:
        raise MechanismError("UNSUPPORTED_GEOMETRY", "rod length must exceed crank radius")
    return -math.asin(r * math.sin(angle) / l)


def sample_times(duration: object, step: object) -> list[float]:
    total = number(duration, "duration_s", positive=True)
    dt = number(step, "sample_interval_s", positive=True)
    ratio = total / dt
    if not math.isfinite(ratio) or ratio > 100000:
        raise MechanismError("INVALID_SAMPLING", "At most 100001 samples are supported")
    count = int(math.floor(ratio + 1e-10))
    if count < 4:
        raise MechanismError("INVALID_SAMPLING", "At least five samples are required")
    if count > 100000:
        raise MechanismError("INVALID_SAMPLING", "At most 100001 samples are supported")
    times = [i * dt for i in range(count + 1)]
    if total - times[-1] > 1e-9:
        raise MechanismError("INVALID_SAMPLING", "sample_interval_s must evenly divide the requested duration")
    times[-1] = total
    return times


def _solve_3x3(matrix: list[list[float]], vector: list[float]) -> tuple[float, float, float]:
    a = [row[:] + [value] for row, value in zip(matrix, vector)]
    for col in range(3):
        pivot = max(range(col, 3), key=lambda row: abs(a[row][col]))
        a[col], a[pivot] = a[pivot], a[col]
        divisor = a[col][col]
        if abs(divisor) < 1e-14:
            raise MechanismError("INVALID_SAMPLING", "Sample times are degenerate")
        for j in range(col, 4):
            a[col][j] /= divisor
        for row in range(3):
            if row == col:
                continue
            factor = a[row][col]
            for j in range(col, 4):
                a[row][j] -= factor * a[col][j]
    return a[0][3], a[1][3], a[2][3]


def differentiated(times: list[float], positions: list[float]) -> tuple[list[float], list[float]]:
    """Five-sample local quadratic least-squares fit, shifted at endpoints."""
    if len(times) != len(positions) or len(times) < 5:
        raise MechanismError("INVALID_SAMPLING", "At least five paired samples are required")
    if any(not math.isfinite(v) for v in times + positions):
        raise MechanismError("NONFINITE_RESULT", "Samples contain NaN or Inf", "failed")
    velocity, acceleration = [], []
    for i, center in enumerate(times):
        start = min(max(i - 2, 0), len(times) - 5)
        scale = (times[start + 4] - times[start]) / 4
        offsets = [(times[j] - center) / scale for j in range(start, start + 5)]
        if any(times[j + 1] <= times[j] for j in range(start, start + 4)):
            raise MechanismError("INVALID_SAMPLING", "Times must increase strictly")
        sums = [sum(u ** power for u in offsets) for power in range(5)]
        rhs = [sum(positions[start + j] * offsets[j] ** power for j in range(5)) for power in range(3)]
        _, b, c = _solve_3x3(
            [[sums[0], sums[1], sums[2]], [sums[1], sums[2], sums[3]], [sums[2], sums[3], sums[4]]], rhs
        )
        velocity.append(b / scale)
        acceleration.append(2 * c / (scale * scale))
    return velocity, acceleration
