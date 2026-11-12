import os
import sys
from pathlib import Path
_viewer_root = Path(__file__).resolve().parent.parent
if str(_viewer_root) not in sys.path:
    sys.path.insert(0, str(_viewer_root))

import json
import time
import base64
import urllib.request
import sqlite3
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from pipeline.config import WORKSPACE_ROOT, DB_PATH, REPORTS_DIR, MIN_FREE_DISK_GB, MANIFEST_JSON_PATH
from pipeline.db import get_connection, log_operation, record_checkpoint

HIGH_PRECISION_STUDY_PHRASES = [
    'page from a notebook',
    'notebook open to a page',
    'open notebook',
    'page of a notebook',
    'math problem',
    'mathematical equations',
    'math equations',
    'handwritten notes',
    'worksheet',
    'textbook page',
    'page from a textbook',
    'exam paper',
    'question paper',
    'homework assignment',
    'graph paper'
]

PERSON_STARTERS = (
    'in the image, a young boy',
    'in the image, a young girl',
    'in the image, a young woman',
    'in the image, a young man',
    'in the image, a man',
    'in the image, a woman',
    'in the image, a baby',
    'in the image, a child',
    'in the image, a toddler',
    'in the image, a couple',
    'in the image, a group of people',
    'the image depicts a couple',
    'the image depicts a group',
    'the image shows a group of people',
    'the image shows a man and',
    'the image shows a woman and',
    'the image shows a family'
)

PERSONAL_EVENT_EXCLUSIONS = [
    'birthday cake', 'cake with', 'standing in front of', 'mountain range',
    'church', 'park', 'beach', 'temple', 'shrine', 'monument', 'wedding'
]

def is_homework_or_document(desc: str, has_faces: bool = False) -> bool:
    # Rule 1: Detected recurring faces in photo -> Disqualified
    if has_faces:
        return False
        
    d = desc.lower().strip()
    
    # Rule 2: Description starts with person/couple/group as main subject -> Disqualified
    if any(d.startswith(starter) for starter in PERSON_STARTERS):
        return False
        
    # Rule 3: Event / vacation / landmark scene -> Disqualified
    if any(ex in d for ex in PERSONAL_EVENT_EXCLUSIONS):
        return False
        
    # Rule 4: Match high-precision study document patterns
    if any(phrase in d for phrase in HIGH_PRECISION_STUDY_PHRASES):
        return True
        
    # Rule 5: Digital screenshot (phone/computer app UI)
    if ('phone screen displaying' in d or 'screenshot of' in d or 'screen displaying' in d):
        if not any(p in d for p in ['holding a phone to take a photo', 'taking a photo with', 'person holding a phone']):
            return True
            
    # Rule 6: Receipts / Invoices / Financial bill
    if ('receipt' in d or 'invoice' in d) and any(w in d for w in ['price', 'total', 'tax', 'store', 'bill']):
        if not any(w in d for w in ['sari', 'baby', 'birthday', 'cake']):
            return True
            
    return False

SUBJECT_MAP = {
    'child': ['child', 'kid', 'boy', 'girl', 'baby', 'toddler'],
    'outdoor': ['park', 'tree', 'trees', 'nature', 'sky', 'mountain', 'water', 'river', 'lake', 'canal', 'grass', 'garden', 'beach'],
    'indoor': ['room', 'desk', 'table', 'chair', 'bed', 'sofa', 'wall', 'living room', 'kitchen', 'hall'],
    'travel': ['temple', 'shrine', 'monument', 'landmark', 'boat', 'street', 'city', 'building', 'historic', 'architecture'],
    'food': ['food', 'meal', 'dinner', 'lunch', 'cake', 'plate', 'dish', 'restaurant', 'tea', 'cup']
}

def recycle_moondream():
    """Force Ollama to terminate llama-server runner, releasing host RAM and Metal VRAM buffers back to macOS."""
    try:
        req = urllib.request.Request(
            'http://127.0.0.1:11434/api/generate',
            data=json.dumps({'model': 'moondream:latest', 'keep_alive': 0}).encode('utf-8'),
            headers={'Content-Type': 'application/json'}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            pass
        time.sleep(0.5)
    except Exception as e:
        print(f"[Memory Watchdog] Warning: failed to recycle runner: {e}", file=sys.stderr)

def query_moondream(img_path: Path, prompt: str = "Describe this image in detail, including all people, objects, activities, text, and setting.") -> Optional[str]:
    try:
        with open(img_path, 'rb') as f:
            b64 = base64.b64encode(f.read()).decode('utf-8')
            
        payload = {
            'model': 'moondream:latest',
            'prompt': prompt,
            'images': [b64],
            'stream': False,
            'options': {
                'num_ctx': 1024,
                'num_predict': 128,
                'temperature': 0
            },
            'keep_alive': '5m'
        }
        
        req = urllib.request.Request(
            'http://127.0.0.1:11434/api/generate',
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'}
        )
        
        with urllib.request.urlopen(req, timeout=45) as resp:
            res = json.loads(resp.read().decode('utf-8'))
            return res.get('response', '').strip()
    except Exception as e:
        print(f"Ollama API error on {img_path.name}: {e}", file=sys.stderr)
        return None

def classify_description(desc: str, item_meta: Dict[str, Any], has_faces: bool = False) -> Dict[str, Any]:
    desc_lower = desc.lower()
    
    # 1. Homework / Document detection (High Precision)
    is_doc = is_homework_or_document(desc, has_faces=has_faces)
    
    # 2. Short caption: first sentence
    sentences = [s.strip() for s in desc.split('.') if s.strip()]
    short_caption = (sentences[0] + '.') if sentences else desc[:120]
    
    # 3. Tags extraction
    tags = set()
    if item_meta.get('city'):
        tags.add(item_meta['city'].lower())
    if item_meta.get('country'):
        tags.add(item_meta['country'].lower())
        
    if is_doc:
        tags.update(['document', 'homework', 'notes', 'study'])
        if any(m in desc_lower for m in ['math', 'equation', 'calculation', 'formula']):
            tags.add('math')
        if any(s in desc_lower for s in ['screenshot', 'screen', 'phone', 'app']):
            tags.add('screenshot')
            
    if has_faces:
        tags.add('people')
        
    primary_subjects = set()
    for tag_name, keywords in SUBJECT_MAP.items():
        if any(k in desc_lower for k in keywords):
            tags.add(tag_name)
            primary_subjects.add(tag_name)
            
    # 4. Scene type determination
    if is_doc:
        scene_type = 'document/study'
    elif 'outdoor' in tags or 'travel' in tags:
        scene_type = 'outdoor'
    elif 'indoor' in tags:
        scene_type = 'indoor'
    elif has_faces:
        scene_type = 'portrait'
    else:
        scene_type = 'general'
        
    return {
        'short_caption': short_caption,
        'detailed_description': desc,
        'tags': json.dumps(sorted(list(tags))),
        'scene_type': scene_type,
        'primary_subjects': json.dumps(sorted(list(primary_subjects))),
        'is_document_or_homework': 1 if is_doc else 0,
        'detected_text': None
    }

def run_vision_pipeline(limit: Optional[int] = None):
    print("=" * 60)
    print("STAGE 4: LOCAL VISION ANALYSIS VIA MOONDREAM")
    print("=" * 60)
    
    conn = get_connection()
    cursor = conn.cursor()
    
    manifest_path = MANIFEST_JSON_PATH
    with open(manifest_path) as f:
        manifest = json.load(f)
        
    print(f"Loaded {len(manifest):,} records from ai_manifest.json.")
    
    # Identify already processed items
    cursor.execute("SELECT media_id FROM ai_descriptions")
    already_described = set(r[0] for r in cursor.fetchall())
    print(f"Already analyzed: {len(already_described):,} images.")
    
    # Find images with faces to enrich tagging
    cursor.execute("SELECT DISTINCT media_id FROM people_faces")
    media_with_faces = set(r[0] for r in cursor.fetchall())
    
    remaining = [item for item in manifest if item['id'] not in already_described]
    if limit:
        remaining = remaining[:limit]
        
    print(f"Remaining images to process: {len(remaining):,}")
    if not remaining:
        print("All images are already analyzed!")
        conn.close()
        return
        
    # Flush runner at startup for clean initial state
    recycle_moondream()

    start_time = time.time()
    batch_records = []
    batch_size = 10
    processed_count = 0
    homework_count = 0
    errors = 0
    now_iso = datetime.now(timezone.utc).isoformat()
    
    try:
        for idx, item in enumerate(remaining, 1):
            mid = item['id']
            proc_path = WORKSPACE_ROOT / item['processing_path']
            
            if not proc_path.exists():
                print(f"Warning: {proc_path} does not exist!", file=sys.stderr)
                errors += 1
                continue
                
            desc = query_moondream(proc_path)
            if desc is None:
                errors += 1
                continue
                
            has_face = mid in media_with_faces
            classified = classify_description(desc, item, has_faces=has_face)
            if classified['is_document_or_homework']:
                homework_count += 1
                
            batch_records.append((
                mid,
                'moondream:latest',
                classified['short_caption'],
                classified['detailed_description'],
                classified['tags'],
                classified['scene_type'],
                classified['primary_subjects'],
                classified['detected_text'],
                classified['is_document_or_homework'],
                desc,
                now_iso
            ))
            processed_count += 1
            
            if len(batch_records) >= batch_size:
                cursor.executemany(
                    """
                    INSERT OR REPLACE INTO ai_descriptions (
                        media_id, model_name, short_caption, detailed_description,
                        tags, scene_type, primary_subjects, detected_text,
                        is_document_or_homework, raw_response, described_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    batch_records
                )
                conn.commit()
                batch_records.clear()
                
                # Active Memory Watchdog: recycle runner every batch of 10 photos.
                # Each photo generates ~162 MiB of prompt cache; recycling every 10 photos
                # ensures accumulated cache stays strictly <= 1.6 GB, and peak process RAM stays <= 3.8 GB.
                recycle_moondream()
                
            if processed_count % 10 == 0 or processed_count == len(remaining):
                elapsed = time.time() - start_time
                rate = processed_count / elapsed if elapsed > 0 else 0
                eta_hours = (len(remaining) - processed_count) / (rate * 3600) if rate > 0 else 0
                print(f"[{processed_count:,}/{len(remaining):,}] {rate:.2f} img/s | ETA: {eta_hours:.1f}h | Flagged HW: {homework_count:,}", flush=True)
                
    except KeyboardInterrupt:
        print("\n[Safe Stop] KeyboardInterrupt caught! Saving current progress...", flush=True)
    finally:
        if batch_records:
            cursor.executemany(
                """
                INSERT OR REPLACE INTO ai_descriptions (
                    media_id, model_name, short_caption, detailed_description,
                    tags, scene_type, primary_subjects, detected_text,
                    is_document_or_homework, raw_response, described_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                batch_records
            )
            conn.commit()
            batch_records.clear()
        # Unload runner from RAM on exit
        recycle_moondream()
        
    total_time = time.time() - start_time
    print(f"\nStage 4 Vision Analysis session finished in {total_time:.1f}s ({total_time/3600:.2f}h).")
    print(f"Total processed: {processed_count:,}, Homework/Doc flagged: {homework_count:,}, Errors: {errors}")
    conn.close()

if __name__ == '__main__':
    limit_arg = int(sys.argv[1]) if len(sys.argv) > 1 else None
    run_vision_pipeline(limit=limit_arg)
