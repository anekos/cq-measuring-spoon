import math

import cadquery as cq
from click_cadquery.git import version_number as ver
from pydantic import BaseModel, Field, field_validator


class Param(BaseModel):
    capacity_ml: float = Field(5.0, description="計量スプーンの容量 (ml)")
    wall_thickness: float = Field(1.6, description="ボウルと持ち手の肉厚 (mm)")
    handle_length: float = Field(90.0, description="持ち手の長さ (mm)")
    handle_width: float = Field(10.0, description="持ち手の幅 (mm)")
    handle_thickness: float = Field(4.0, description="持ち手の厚さ (mm)")

    @field_validator(
        "capacity_ml",
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

    @property
    def filename(self) -> str:
        return f"v{ver()}-{self.capacity_ml:g}ml.stl"


def _fit_fontsize(label: str, max_width: float, max_length: float) -> float:
    # The `fontsize` argument to text() is a nominal em-size, not the actual rendered
    # glyph bounding box, so fitting a label snugly needs measuring one to get the
    # actual size-to-bbox ratio and scaling from that.
    probe_size = 10.0
    probe = cq.Workplane("XY").text(label, probe_size, 1.0, combine=False).val()
    assert isinstance(probe, cq.Shape)
    bbox = probe.BoundingBox()
    scale = min(max_width / bbox.ylen, max_length / bbox.xlen)
    return probe_size * scale


def _bowl_radius(param: Param) -> float:
    # The cavity left by shelling a hemisphere (top face open) inward by wall_thickness
    # is itself an exact concentric hemisphere of radius (radius - wall_thickness) -
    # verified against the actual OCCT-shelled solid's volume, so this closed form needs
    # no numeric solve.
    target_volume = param.capacity_ml * 1000.0
    inner_radius = (3 * target_volume / (2 * math.pi)) ** (1 / 3)
    return inner_radius + param.wall_thickness


def build(param: Param) -> cq.Workplane:
    radius = _bowl_radius(param)

    bowl = (
        cq.Workplane("XY")
        .sphere(radius, angle1=-90, angle2=0, angle3=360)
        .faces(">Z")
        .shell(-param.wall_thickness, kind="intersection")
    )

    # A flat-fronted handle butting into the round rim only touches the wall where the
    # handle's box footprint overlaps the outer sphere (radius); going past the inner
    # wall radius (radius - wall_thickness) means part of that footprint falls inside the
    # hollow cavity instead of the wall. Embedding just past that radius keeps every
    # point of the front face inside solid material (its distance from the bowl center is
    # never less than the inner radius) while maximizing the contact width available.
    inner_radius = radius - param.wall_thickness
    handle_near_x = inner_radius + param.wall_thickness * 0.3
    if handle_near_x >= radius:
        raise ValueError(
            "wall_thickness is too large relative to the bowl to attach a handle"
        )

    max_handle_width = 2 * math.sqrt(radius**2 - handle_near_x**2)
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

    # Engrave the capacity on the handle's z=-handle_thickness face: after the final
    # 180° flip that face becomes the top-facing one, so the label reads right-side up
    # and isn't printed face-down against the bed.
    label = f"{param.capacity_ml:g}ml"
    engrave_depth = min(0.6, param.handle_thickness * 0.3)
    fontsize = _fit_fontsize(label, param.handle_width * 0.9, param.handle_length * 0.6)
    handle = (
        handle.faces("<Z")
        .workplane(centerOption="CenterOfBoundBox")
        .text(label, fontsize, -engrave_depth)
    )

    result = bowl.union(handle)

    # The bowl rim (z=0) and the handle's top face (also z=0) share one flat plane, and
    # the bowl narrows going down (z<0). Flipping 180° about X puts that shared plane on
    # the bed and the bowl's closed bottom on top, so it prints support-free: the wall
    # tapers inward going up (self-supporting) and the handle lies flat on the bed.
    return result.rotate((0, 0, 0), (1, 0, 0), 180)
