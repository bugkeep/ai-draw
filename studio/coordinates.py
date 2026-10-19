"""Optional image-pixel input; prepared drawing actions always use canvas units."""

from pydantic import BaseModel, ConfigDict, Field


class ObservationCoordinates(BaseModel):
    model_config = ConfigDict(extra='forbid')

    observation_id: str | None = Field(default=None, pattern=r'^view_[a-f0-9]{32}$',
        description='Optional ID of a CURRENT canvas PNG already viewed in your own context. '
                    'When set, points use that PNG\'s original pixel coordinates (top-left=0,0; '
                    'see its width/height), and the runtime maps them to canvas units. '
                    'Do not convert coordinates yourself. draw_paths origin/scale must be omitted; '
                    'eraser radius also uses image pixels; strokeWidth remains a native brush unit. '
                    'An image from another Agent/task, a proposed result, an unseen image or an older '
                    'scene is rejected. Omit this field for ordinary absolute canvas points.')


def map_observed_arguments(name: str, arguments: dict, metadata: dict) -> dict:
    """Map validated tool input using the exact rendered PNG, including rounded dimensions."""
    bounds, density = metadata['scene_bounds'], metadata['pixels_per_unit']

    def points(values):
        if any(not 0 <= x <= metadata['width'] or not 0 <= y <= metadata['height'] for x, y in values):
            raise ValueError('Image points must stay inside the observed PNG width/height. '
                             'Use absolute canvas coordinates to draw outside this image.')
        return [(bounds['x'] + x / density['x'], bounds['y'] + y / density['y']) for x, y in values]

    mapped = {key: value for key, value in arguments.items() if key != 'observation_id'}
    if name == 'draw_paths':
        if tuple(arguments.get('origin', (0, 0))) != (0, 0) or arguments.get('scale', 1) != 1:
            raise ValueError('Image-pixel points cannot also use draw_paths origin/scale')
        mapped['paths'] = [{**path, 'points': points(path['points'])} for path in arguments['paths']]
    elif name in {'reshape_path', 'erase_strokes'}:
        mapped['points'] = points(arguments['points'])
        if name == 'erase_strokes':
            # Integer PNG dimensions can differ slightly in x/y density. Cover
            # the requested pixel radius along both axes with the canvas brush.
            mapped['radius'] = arguments.get('radius', 8) / min(density.values())
    else:
        raise ValueError('Image coordinates are available only for draw_paths, reshape_path and erase_strokes')
    return mapped
