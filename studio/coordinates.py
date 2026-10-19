"""Optional image coordinates; prepared drawing actions always use canvas units."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ObservationCoordinates(BaseModel):
    model_config = ConfigDict(extra='forbid')

    observation_id: str | None = Field(default=None, pattern=r'^view_[a-f0-9]{32}$',
        description='Optional ID of a CURRENT canvas PNG already viewed in your own context. '
                    'With coordinate_space=image_normalized (recommended), points use 0..1000 along EACH '
                    'image axis; top-left=(0,0), bottom-right=(1000,1000). Runtime maps them to canvas. '
                    'Omitting coordinate_space preserves the original image-pixel input. '
                    'draw_paths origin/scale must be omitted; strokeWidth remains a native brush unit. '
                    'An image from another Agent/task, a proposed result, an unseen image or an older '
                    'scene is rejected. Omit this field for ordinary absolute canvas points.')
    coordinate_space: Literal['image_pixels', 'image_normalized'] | None = Field(default=None,
        description='Use image_normalized for visual edits: both axes span 0..1000 regardless of PNG dimensions. '
                    'Requires observation_id. image_pixels uses the PNG\'s original width/height instead. '
                    'For erasing, normalized radius is a fraction of the longer image side (1000=full side), '
                    'so the brush stays circular. Default without this field is image_pixels when an observation_id '
                    'is supplied, otherwise ordinary absolute canvas coordinates.')

    @model_validator(mode='after')
    def check_coordinate_frame(self):
        if self.coordinate_space and not (self.observation_id or getattr(self, 'reference_view_id', None)):
            raise ValueError('coordinate_space requires an observation_id or a complete viewed reference mapping')
        return self


class ReferenceCoordinates(ObservationCoordinates):
    reference_view_id: str | None = Field(default=None, pattern=r'^refview_[a-f0-9]{32}$',
        description='Optional reference PNG you have actually viewed in your OWN context. '
                    'Use together with reference_layout_id and coordinate_space=image_normalized to place '
                    'anchors on the reference itself, including a source close-up. Runtime maps them through '
                    'the main Agent\'s common layout to canvas. Do not combine with observation_id.')
    reference_layout_id: str | None = Field(default=None, pattern=r'^layout_[a-f0-9]{32}$',
        description='Immutable source_frame/target mapping created by the main Agent using create_reference_layout. '
                    'All children drawing the same source composition use this same layout ID. '
                    'Reference anchors retain the source proportions; use current canvas observations for edits '
                    'after an object has been moved away from that layout.')

    @model_validator(mode='after')
    def check_reference_frame(self):
        if self.reference_view_id or self.reference_layout_id:
            if not self.reference_view_id or not self.reference_layout_id:
                raise ValueError('Reference coordinates require both reference_view_id and reference_layout_id')
            if self.observation_id:
                raise ValueError('Choose one coordinate source: current canvas observation or reference layout')
            if self.coordinate_space != 'image_normalized':
                raise ValueError('Reference coordinates require coordinate_space=image_normalized')
        return self


def map_observed_arguments(name: str, arguments: dict, metadata: dict) -> dict:
    """Map validated tool input using the exact rendered PNG, including rounded dimensions."""
    bounds, density = metadata['scene_bounds'], metadata['pixels_per_unit']
    normalized = arguments.get('coordinate_space') == 'image_normalized'
    width, height = (1000, 1000) if normalized else (metadata['width'], metadata['height'])
    scale_x = bounds['width'] / 1000 if normalized else 1 / density['x']
    scale_y = bounds['height'] / 1000 if normalized else 1 / density['y']

    def points(values):
        if any(not 0 <= x <= width or not 0 <= y <= height for x, y in values):
            extent = 'normalized frame (0..1000 on both axes)' if normalized else 'PNG width/height'
            raise ValueError(f'Image points must stay inside the observed {extent}. '
                             'Use absolute canvas coordinates to draw outside this image.')
        return [(bounds['x'] + x * scale_x, bounds['y'] + y * scale_y) for x, y in values]

    mapped = {key: value for key, value in arguments.items() if key not in {
        'observation_id', 'coordinate_space', 'reference_view_id', 'reference_layout_id'}}
    if name == 'draw_paths':
        if tuple(arguments.get('origin', (0, 0))) != (0, 0) or arguments.get('scale', 1) != 1:
            raise ValueError('Image-pixel points cannot also use draw_paths origin/scale')
        mapped['paths'] = [{**path, 'points': points(path['points'])} for path in arguments['paths']]
    elif name in {'reshape_path', 'erase_strokes'}:
        mapped['points'] = points(arguments['points'])
        if name == 'erase_strokes':
            # Integer PNG dimensions can differ slightly in x/y density. Cover
            # the requested pixel radius along both axes with the canvas brush.
            mapped['radius'] = arguments.get('radius', 8) * (max(bounds['width'], bounds['height']) / 1000
                                                           if normalized else 1 / min(density.values()))
    else:
        raise ValueError('Image coordinates are available only for draw_paths, reshape_path and erase_strokes')
    return mapped
