

import * as h3 from "h3-js";
import * as turf from "@turf/turf";
import { getMapZoom, getMapBounds, drawHexagons, IS_PROD } from "./main.js";

// never allow h3 resolution to be lower than this
const MIN_RESOLUTION = 6;

// target resolution for hexagons
const TARGET_RESOLUTION = 2;

function expandBounds(bounds, h3Resolution) {
    const hexSize = h3.getHexagonEdgeLengthAvg(h3Resolution, "km");
    const hexSizeInDegrees = turf.lengthToDegrees(hexSize, "kilometers");
    const expandedBounds = {
        minLat: bounds.minLat - hexSizeInDegrees,
        minLon: bounds.minLon - hexSizeInDegrees,
        maxLat: bounds.maxLat + hexSizeInDegrees,
        maxLon: bounds.maxLon + hexSizeInDegrees,
    };
    return expandedBounds;
}

function optimizeHexagons(hexagons) {
    // get a set of hexagons running h3.cellToParent(cell, TARGET_RESOLUTION) on each
    // and return a unique set of them
    const optimizedHexagons = new Set();

    if (hexagons.length > 0 && h3.getResolution(hexagons[0]) < TARGET_RESOLUTION) {
        // just use the hexagons as they are
        return hexagons;
    } else {
        hexagons.forEach((cell) => {
            const parent = h3.cellToParent(cell, TARGET_RESOLUTION);
            optimizedHexagons.add(parent);
        });
    }

    // compact the hexagons
    const compactedHexagons = h3.compactCells(
        [...optimizedHexagons],
        TARGET_RESOLUTION
    );
    return compactedHexagons;
}

function adjustToRange(value, min, max) {
    const range = max - min;
    return ((((value - min) % range) + range) % range) + min;
}

function adjustLongitude(longitude) {
    return adjustToRange(longitude, -180, 180);
}

const splitPolygon = ({ minLat, minLon, maxLat, maxLon }) =>
    splitLongitudeRange(minLon, maxLon).map(
        ([segmentMinLon, segmentMaxLon]) => ({
            minLat,
            minLon: segmentMinLon, // Use the segment's min longitude
            maxLat,
            maxLon: segmentMaxLon, // Use the segment's max longitude
        })
    );

function splitLongitudeRange(
    minLon,
    maxLon
) {
    const result = [];

    let curPos = minLon;

    const adjustedMax = adjustLongitude(maxLon);

    while (curPos < maxLon) {
        const normalized = adjustLongitude(curPos);
        const next = normalized < 0 ? 0 : 180;
        curPos = curPos + next - normalized;
        if (curPos > maxLon) {
            result.push([normalized, adjustedMax]);
        } else {
            result.push([normalized, next]);
        }
    }

    return result;
}

const boundsToPolygon = ({ minLat, minLon, maxLat, maxLon }) => [
    [minLon, minLat],
    [maxLon, minLat],
    [maxLon, maxLat],
    [minLon, maxLat],
    [minLon, minLat],
];

export const ZOOM_TO_RESOLUTION = {
    0: 0,
    1: 0,
    2: 0,
    3: 1,
    4: 1,
    5: 2,
    6: 3,
    7: 4,
    8: 4,
    9: 5,
    10: 5,
    11: 6,
    12: 7,
    13: 8,
    14: 9,
    15: 10,
    16: 10,
    17: 11,
    18: 11,
    19: 12,
    20: 13,
    21: 14,
    22: 14,
    23: 14,
    24: 14,
};

export const RESOLUTION_TO_ZOOM = Object.entries(ZOOM_TO_RESOLUTION).reduce((acc, [k, v]) => ({ ...acc, [v]: +k }), {});

const getHexagons = (bounds, resolution) => {
    const all_bounds = splitPolygon(bounds);

    const polygons = all_bounds.map(boundsToPolygon);

    const hexagons = [
        ...new Set(
            polygons.flatMap((polygon) =>
                h3.polygonToCells(polygon, resolution, true)
            )
        ),
    ];

    return hexagons;
}

export function getViewportHexagons() {
    if (!IS_PROD) console.time("getViewportHexagons");
    const zoom = getMapZoom();
    const resolution = Math.min(ZOOM_TO_RESOLUTION[Math.round(zoom)], MIN_RESOLUTION);
    // const bounds = getVisibleBounds(viewState);
    const bounds = getMapBounds();
    const expandedBounds = expandBounds(bounds, resolution);
    const hexagons = getHexagons(expandedBounds, resolution);
    const compactedHexagons = optimizeHexagons(hexagons, resolution);

    if (!IS_PROD) console.timeEnd("getViewportHexagons");

    if (!IS_PROD) drawHexagons(compactedHexagons);

    return compactedHexagons;
}

export function compressedCells(compactedHexagons) {
    // take each cell_id (string) and compress the total package of them
    // to a single string for minimal data transfer

    const compressed = compactedHexagons.reduce((acc, cell) => {
        const hex = h3.h3ToString(cell);
        return acc + hex;
    }, "");
}