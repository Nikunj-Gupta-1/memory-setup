from __future__ import annotations

from memory_setup.ingestion.companions import group_companions


def test_group_companions_pairs_live_photos_and_sidecars() -> None:
    items = [
        {"dir_rel": "2024", "original_stem": "IMG_1001", "media_type": "photo", "content_identifier": None},
        {"dir_rel": "2024", "original_stem": "IMG_1001", "media_type": "video", "content_identifier": None},
        {"dir_rel": "2024", "original_stem": "IMG_1001", "media_type": "sidecar", "content_identifier": None},
        {"dir_rel": "2024", "original_stem": "IMG_2002", "media_type": "photo", "content_identifier": "UUID-AAA"},
        {"dir_rel": "exports", "original_stem": "clip_uuid", "media_type": "video", "content_identifier": "UUID-AAA"},
        {"dir_rel": "2024", "original_stem": "SOLO_999", "media_type": "photo", "content_identifier": None},
    ]

    linked = group_companions(items, start_seq=1)
    assert linked == 5
    assert items[0]["companion_role"] == "primary"
    assert items[1]["companion_role"] == "live_photo_video"
    assert items[2]["companion_role"] == "sidecar_aae"
    assert items[0]["companion_group_id"] == items[1]["companion_group_id"] == items[2]["companion_group_id"]

    # UUID match across folders
    assert items[3]["companion_group_id"] == items[4]["companion_group_id"]
    assert items[4]["companion_role"] == "live_photo_video"

    # Unpaired item
    assert items[5]["companion_group_id"] is None
    assert items[5]["companion_role"] == "none"
