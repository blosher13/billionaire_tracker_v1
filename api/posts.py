"""Stored X posts, each with a place on the map.

Kafka Streams places a post at the jet's position at or before the post time (matched or
last_known). When the jet had no recorded position yet (the post predates tracking), Streams
can't look forward, so those posts arrive as "unknown". Here they get the jet's recorded
position closest in time to the post instead, with location_status "nearest" and
position_offset_s showing how far apart the two were (negative = position after the post).

Use PLACED_POSTS in place of the x_posts table in a FROM clause.
"""

PLACED_POSTS = """(
    SELECT p.post_id, p.hex, p.username, p.owner, p.text, p.post_type, p.url,
           p.like_count, p.reply_count, p.repost_count, p.view_count, p.created_at,
           CASE WHEN p.location_status = 'unknown' AND f.ts IS NOT NULL THEN 'nearest'
                ELSE p.location_status END AS location_status,
           COALESCE(p.plane_lat, f.lat) AS plane_lat,
           COALESCE(p.plane_lon, f.lon) AS plane_lon,
           COALESCE(p.plane_altitude_ft, f.altitude_ft) AS plane_altitude_ft,
           COALESCE(p.plane_on_ground, f.on_ground) AS plane_on_ground,
           COALESCE(p.plane_ts, f.ts) AS plane_ts,
           COALESCE(p.position_offset_s, extract(epoch FROM p.created_at - f.ts)::bigint) AS position_offset_s
    FROM x_posts p
    LEFT JOIN LATERAL (
        SELECT ts, lat, lon, altitude_ft, on_ground FROM flight_enriched e
        WHERE p.location_status = 'unknown' AND e.hex = p.hex
        ORDER BY abs(extract(epoch FROM e.ts - p.created_at))
        LIMIT 1
    ) f ON true
) AS x_posts"""
