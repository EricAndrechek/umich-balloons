from typing import Set
import h3
from ..models.models import Bbox

H3_RESOLUTION = 7

def get_cells_for_bbox(bbox: Bbox) -> Set[str]:
    """
    Determines the set of H3 cell IDs at the configured resolution
    that intersect the given bounding box.
    """
    # H3 needs the bounding box corners in GeoJSON format [lon, lat]
    # Ensure order: SW, SE, NE, NW, SW (closed loop)
    polygon_coordinates = [
        (bbox.minLat, bbox.minLon),  # SW
        (bbox.minLat, bbox.maxLon),  # SE
        (bbox.maxLat, bbox.maxLon),  # NE
        (bbox.maxLat, bbox.minLon),  # NW
        (bbox.minLat, bbox.minLon),  # Close loop back to SW
    ]

    try:
        # get H3 cells that intersect the polygon
        poly = h3.LatLngPoly(polygon_coordinates)
        cells_set = set(h3.h3shape_to_cells(poly, res=H3_RESOLUTION))
        return cells_set
    except Exception as e:
        # H3 library can raise errors on invalid input
        print(f"Error calculating H3 cells for bbox {bbox}: {e}")
        return set()  # Return empty set on error
