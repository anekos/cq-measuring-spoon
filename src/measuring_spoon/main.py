import math

import cadquery as cq
from click_cadquery.git import version_number as ver
from pydantic import BaseModel, Field, field_validator, model_validator

_BISECTION_ITERATIONS = 50


class Param(BaseModel):
    capacity_ml: float = Field(5.0, description="計量スプーンの容量 (ml)")
    depth_ratio: float = Field(0.6, description="ボウルの深さ / 上端半径")
    taper_angle_deg: float = Field(10.0, description="ボウル壁の傾斜角 (度、0=垂直)")
    wall_thickness: float = Field(1.6, description="ボウルと持ち手の肉厚 (mm)")
    handle_length: float = Field(90.0, description="持ち手の長さ (mm)")
    handle_width: float = Field(10.0, description="持ち手の幅 (mm)")
    handle_thickness: float = Field(4.0, description="持ち手の厚さ (mm)")

    @field_validator(
        "capacity_ml",
        "depth_ratio",
        "wall_thickness",
        "handle_length",
        "handle_width",
        "handle_thickness",
    )
    @classmethod
    def _positive(cls, value: float, info) -> float:
        if value <= 0:
            raise ValueError(f"{info.field_name} must be positive, got {value}")
        return value

    @field_validator("taper_angle_deg")
    @classmethod
    def _valid_taper_angle(cls, value: float) -> float:
        if not (0 <= value < 90):
            raise ValueError(f"taper_angle_deg must be in [0, 90), got {value}")
        return value

    @model_validator(mode="after")
    def _bowl_geometry_is_valid(self) -> "Param":
        margin = self.depth_ratio * math.tan(math.radians(self.taper_angle_deg))
        if margin >= 1:
            raise ValueError(
                "depth_ratio and taper_angle_deg combination makes the bowl bottom "
                f"radius non-positive (depth_ratio * tan(taper_angle_deg) = {margin:.3f} >= 1)"
            )
        return self

    @property
    def filename(self) -> str:
        return f"v{ver()}-{self.capacity_ml:g}ml.stl"


def _bowl_shape(top_radius: float, bottom_radius: float, height: float) -> cq.Workplane:
    return (
        cq.Workplane("XY")
        .circle(top_radius)
        .workplane(offset=-height)
        .circle(bottom_radius)
        .loft()
    )


def _volume(wp: cq.Workplane) -> float:
    shape = wp.val()
    assert isinstance(shape, cq.Shape)
    return shape.Volume()


def _bowl_cavity_volume(top_radius: float, param: Param) -> float | None:
    height = param.depth_ratio * top_radius
    bottom_radius = top_radius - height * math.tan(math.radians(param.taper_angle_deg))
    outer_volume = (
        math.pi
        * height
        / 3
        * (top_radius**2 + top_radius * bottom_radius + bottom_radius**2)
    )
    try:
        shelled = (
            _bowl_shape(top_radius, bottom_radius, height)
            .faces(">Z")
            .shell(-param.wall_thickness, kind="intersection")
        )
    except Exception:  # noqa: BLE001 - any OCCT failure here just means this candidate size is infeasible
        return None
    return outer_volume - _volume(shelled)


def _solve_bowl_top_radius(param: Param) -> float:
    target_volume = param.capacity_ml * 1000.0

    lo = param.wall_thickness
    hi = param.wall_thickness * 4
    while True:
        cavity = _bowl_cavity_volume(hi, param)
        if cavity is not None and cavity >= target_volume:
            break
        hi *= 1.5
        if hi > 1e5:
            raise ValueError(
                f"capacity_ml={param.capacity_ml} is too large to solve for a bowl size"
            )

    for _ in range(_BISECTION_ITERATIONS):
        mid = (lo + hi) / 2
        cavity = _bowl_cavity_volume(mid, param)
        if cavity is None or cavity < target_volume:
            lo = mid
        else:
            hi = mid

    return (lo + hi) / 2


def build(param: Param) -> cq.Workplane:
    top_radius = _solve_bowl_top_radius(param)
    angle = math.radians(param.taper_angle_deg)
    height = param.depth_ratio * top_radius
    bottom_radius = top_radius - height * math.tan(angle)

    bowl = (
        _bowl_shape(top_radius, bottom_radius, height)
        .faces(">Z")
        .shell(-param.wall_thickness, kind="intersection")
    )

    # A flat-fronted handle butting into the round rim only touches the wall where the
    # handle's box footprint overlaps the outer disk (radius top_radius); going past the
    # inner wall radius (inner_radius) means part of that footprint falls inside the
    # hollow cavity instead of the wall. Embedding just past inner_radius keeps every
    # point of the front face inside solid material (its distance from the bowl axis is
    # never less than inner_radius) while maximizing the contact width available.
    inner_radius = top_radius - param.wall_thickness / math.cos(angle)
    handle_near_x = inner_radius + param.wall_thickness * 0.3
    if handle_near_x >= top_radius:
        raise ValueError(
            "wall_thickness is too large relative to the bowl to attach a handle"
        )

    max_handle_width = 2 * math.sqrt(top_radius**2 - handle_near_x**2)
    if param.handle_width > max_handle_width:
        raise ValueError(
            f"handle_width={param.handle_width} is too wide to fully touch the bowl rim "
            f"(max {max_handle_width:.1f}mm for capacity_ml={param.capacity_ml}); "
            "reduce handle_width, or grow the bowl via capacity_ml/wall_thickness"
        )

    handle = (
        cq.Workplane("XY")
        .box(
            param.handle_length,
            param.handle_width,
            param.handle_thickness,
            centered=(False, True, True),
        )
        .translate((handle_near_x, 0, -param.handle_thickness / 2))
    )

    result = bowl.union(handle)

    # The bowl rim (z=0) and the handle's top face (also z=0) share one flat plane, and
    # the bowl narrows going down (z<0). Flipping 180° about X puts that shared plane on
    # the bed and the bowl's closed bottom on top, so it prints support-free: the wall
    # tapers inward going up (self-supporting) and the handle lies flat on the bed.
    return result.rotate((0, 0, 0), (1, 0, 0), 180)
