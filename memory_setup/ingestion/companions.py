from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List


def group_companions(items: List[Dict[str, Any]], start_seq: int = 1) -> int:
    """
    Pair Apple Live Photos (.mov), .aae/.xmp sidecars, and .wav audio memos with primary photos.
    First pairs by ContentIdentifier / MediaGroupUUID if available, then by (dir_rel, stem_lower).
    Returns number of items linked into companion groups.
    """
    uuid_map: Dict[str, List[int]] = defaultdict(list)
    unlinked_indices: List[int] = []

    for idx, item in enumerate(items):
        content_id = item.get("content_identifier")
        if content_id:
            uuid_map[str(content_id)].append(idx)
        else:
            unlinked_indices.append(idx)

    dir_stem_map: Dict[tuple, List[int]] = defaultdict(list)
    for idx in unlinked_indices:
        key = (item_dir := items[idx].get("dir_rel", ""), items[idx]["original_stem"].lower())
        _ = item_dir
        dir_stem_map[key].append(idx)

    all_clusters: List[List[int]] = []
    for indices in uuid_map.values():
        if len(indices) > 1:
            all_clusters.append(indices)
        else:
            idx = indices[0]
            key = (items[idx].get("dir_rel", ""), items[idx]["original_stem"].lower())
            dir_stem_map[key].append(idx)

    for indices in dir_stem_map.values():
        if len(indices) > 1:
            all_clusters.append(indices)
        else:
            idx = indices[0]
            items[idx]["companion_group_id"] = None
            items[idx]["companion_role"] = "none"

    group_seq = start_seq
    linked_count = 0
    for indices in all_clusters:
        group_id = f"GRP_{group_seq:05d}"
        group_seq += 1
        has_photo = any(items[i]["media_type"] == "photo" for i in indices)

        for i in indices:
            items[i]["companion_group_id"] = group_id
            mtype = items[i]["media_type"]
            if mtype == "photo":
                items[i]["companion_role"] = "primary"
            elif mtype == "video" and has_photo:
                items[i]["companion_role"] = "live_photo_video"
            elif mtype == "sidecar":
                items[i]["companion_role"] = "sidecar_aae"
            elif mtype == "audio_memo":
                items[i]["companion_role"] = "audio_memo"
            else:
                items[i]["companion_role"] = "companion"
            linked_count += 1

    return linked_count
