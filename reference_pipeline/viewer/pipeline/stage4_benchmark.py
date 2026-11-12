import os
import sys
import json
import time
import base64
import subprocess
import urllib.request
from pathlib import Path
from typing import Dict, Any, List

from pipeline.config import WORKSPACE_ROOT, DB_PATH, REPORTS_DIR

DOCUMENT_KEYWORDS = [
    'notebook', 'handwriting', 'handwritten', 'math', 'maths', 'equation',
    'calculation', 'textbook', 'homework', 'worksheet', 'study notes',
    'piece of paper', 'page with', 'sheet of paper', 'written on it',
    'document', 'receipt', 'invoice', 'diagram', 'formula', 'graph paper',
    'blackboard', 'whiteboard', 'screenshot', 'computer screen', 'phone screen'
]

CALIBRATION_IDS = [
    'P_000001', # 2008: Child in traditional Indian attire
    'P_000500', # 2010: Early childhood / family
    'P_001000', # 2012: Family life
    'P_001500', # 2012: Portrait
    'P_005000', # 2016: Venice, Italy (Historic architecture / canal)
    'P_005180', # 2016: Zurich, Switzerland (6-member family portrait)
    'P_007000', # 2016: Family gathering
    'P_010000', # 2018: Varanasi, India (Ghats / cultural)
    'P_015000', # 2019: Portrait / outdoor
    'P_018000', # 2020: Delhi, India (Indoor / family)
    'P_024073', # 2022: Notebook page 1
    'P_024075', # 2022: Notebook math homework page 2
    'P_024076', # 2022: Notebook math homework page 3
    'P_028000', # 2025: Screenshot / digital document
    'P_030000', # 2026: Tokyo, Japan (Travel / street scene)
]

def query_moondream(img_path: str, prompt: str = "Describe this image in detail, including all people, objects, activities, text, and setting.") -> Dict[str, Any]:
    with open(img_path, 'rb') as f:
        b64 = base64.b64encode(f.read()).decode('utf-8')
    
    payload = {
        'model': 'moondream:latest',
        'prompt': prompt,
        'images': [b64],
        'stream': False
    }
    
    req = urllib.request.Request(
        'http://127.0.0.1:11434/api/generate',
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'}
    )
    
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=45) as resp:
        res = json.loads(resp.read().decode('utf-8'))
    t1 = time.perf_counter()
    
    return {
        'response': res.get('response', '').strip(),
        'duration_s': t1 - t0,
        'eval_count': res.get('eval_count', 0),
        'eval_duration_s': (res.get('eval_duration', 0) / 1e9)
    }

def detect_faces_native(img_path: str) -> List[Dict[str, Any]]:
    bin_path = WORKSPACE_ROOT / 'bin_detect_faces'
    if not bin_path.exists():
        return []
    try:
        proc = subprocess.run([str(bin_path), img_path], capture_output=True, text=True, timeout=10)
        if proc.returncode == 0:
            return json.loads(proc.stdout)
    except Exception as e:
        print(f"Face detect error: {e}", file=sys.stderr)
    return []

def classify_and_tag(desc: str, item_meta: Dict[str, Any], faces: List[Dict[str, Any]]) -> Dict[str, Any]:
    desc_lower = desc.lower()
    
    # Document / Homework detection
    is_doc = any(k in desc_lower for k in DOCUMENT_KEYWORDS)
    
    # Short caption: first sentence
    sentences = [s.strip() for s in desc.split('.') if s.strip()]
    short_caption = (sentences[0] + '.') if sentences else desc[:120]
    
    # Tags extraction
    tags = set()
    if item_meta.get('city'):
        tags.add(item_meta['city'].lower())
    if item_meta.get('country'):
        tags.add(item_meta['country'].lower())
    
    if is_doc:
        tags.update(['document', 'homework', 'notes', 'study'])
        if 'math' in desc_lower or 'equation' in desc_lower or 'calculation' in desc_lower:
            tags.add('math')
        if 'screenshot' in desc_lower or 'screen' in desc_lower:
            tags.add('screenshot')
    
    # People & Face tags
    face_count = len(faces)
    if face_count > 0:
        tags.add('people')
        if face_count == 1:
            tags.add('solo-portrait')
        elif face_count >= 3:
            tags.add('group-photo')
            tags.add('family')
    
    # Common subject tags
    subject_map = {
        'child': ['child', 'kid', 'boy', 'girl', 'baby', 'toddler'],
        'outdoor': ['park', 'tree', 'trees', 'nature', 'sky', 'mountain', 'water', 'river', 'lake', 'canal', 'grass', 'garden'],
        'indoor': ['room', 'desk', 'table', 'chair', 'bed', 'sofa', 'wall', 'living room', 'kitchen'],
        'travel': ['temple', 'shrine', 'monument', 'landmark', 'boat', 'street', 'city', 'building', 'historic'],
        'food': ['food', 'meal', 'dinner', 'lunch', 'cake', 'plate', 'dish', 'restaurant']
    }
    
    for tag_name, keywords in subject_map.items():
        if any(k in desc_lower for k in keywords):
            tags.add(tag_name)
    
    # Scene type determination
    if is_doc:
        scene_type = 'document/study'
    elif 'outdoor' in tags or 'travel' in tags:
        scene_type = 'outdoor'
    elif 'indoor' in tags:
        scene_type = 'indoor'
    elif face_count > 0:
        scene_type = 'portrait'
    else:
        scene_type = 'general'
        
    return {
        'short_caption': short_caption,
        'detailed_description': desc,
        'is_document_or_homework': 1 if is_doc else 0,
        'scene_type': scene_type,
        'face_count': face_count,
        'faces': faces,
        'tags': sorted(list(tags))
    }

def run_benchmark():
    manifest_path = WORKSPACE_ROOT / 'ai_manifest.json'
    with open(manifest_path) as f:
        manifest = json.load(f)
    manifest_by_id = {item['id']: item for item in manifest}
    
    results = []
    total_time = 0.0
    print(f"Starting Stage 4 Calibration Benchmark on {len(CALIBRATION_IDS)} images...")
    
    for idx, sid in enumerate(CALIBRATION_IDS, 1):
        item = manifest_by_id.get(sid)
        if not item:
            print(f"[{idx}/{len(CALIBRATION_IDS)}] Warning: {sid} not found in manifest!")
            continue
            
        img_path = str(WORKSPACE_ROOT / item['processing_path'])
        print(f"[{idx}/{len(CALIBRATION_IDS)}] Analyzing {sid} ({item['current_path']})...", end='', flush=True)
        
        # Face detection
        faces = detect_faces_native(img_path)
        
        # Moondream query
        res = query_moondream(img_path)
        dur = res['duration_s']
        total_time += dur
        
        # Classification & tagging
        classified = classify_and_tag(res['response'], item, faces)
        
        entry = {
            'media_id': sid,
            'current_path': item['current_path'],
            'processing_path': item['processing_path'],
            'capture_timestamp': item.get('capture_timestamp'),
            'city': item.get('city'),
            'country': item.get('country'),
            'camera': f"{item.get('camera_make', '')} {item.get('camera_model', '')}".strip() or 'Unknown',
            'inference_time_s': round(dur, 2),
            'short_caption': classified['short_caption'],
            'detailed_description': classified['detailed_description'],
            'is_document_or_homework': classified['is_document_or_homework'],
            'scene_type': classified['scene_type'],
            'face_count': classified['face_count'],
            'faces': classified['faces'],
            'tags': classified['tags']
        }
        results.append(entry)
        doc_flag = " [DOC/HOMEWORK]" if entry['is_document_or_homework'] else ""
        print(f" Done ({dur:.2f}s | {len(faces)} faces){doc_flag}")
        
    avg_time = total_time / len(results) if results else 0
    print(f"\nCalibration Completed: {len(results)} images in {total_time:.1f}s (Average: {avg_time:.2f}s/image)")
    
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    json_path = REPORTS_DIR / 'stage4_calibration_benchmark.json'
    with open(json_path, 'w') as f:
        json.dump({
            'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'model': 'moondream:latest',
            'total_samples': len(results),
            'avg_inference_time_s': round(avg_time, 2),
            'samples': results
        }, f, indent=2)
        
    # Generate Markdown Report
    md_path = REPORTS_DIR / 'stage4_calibration_benchmark.md'
    with open(md_path, 'w') as f:
        f.write("# Stage 4 Vision Model Calibration Benchmark Report\n\n")
        f.write(f"- **Evaluated Model**: `moondream:latest` via Ollama\n")
        f.write(f"- **Face Detector**: Native Apple Silicon Vision (`bin_detect_faces`)\n")
        f.write(f"- **Sample Size**: {len(results)} diverse images (2008–2026)\n")
        f.write(f"- **Average Inference Time**: **{avg_time:.2f} seconds/image** (~{round(avg_time*32424/3600, 1)} hours full archive)\n\n")
        f.write("## Calibration Samples & Analysis\n\n")
        f.write("| Media ID | Capture | Location | Faces | Doc/HW? | Latency | Short Caption |\n")
        f.write("|---|---|---|---|---|---|---|\n")
        for r in results:
            loc = f"{r['city']}, {r['country']}" if r['city'] else "—"
            doc_badge = "✅ **YES**" if r['is_document_or_homework'] else "No"
            f.write(f"| `{r['media_id']}` | {r['capture_timestamp'][:10] if r['capture_timestamp'] else 'Unknown'} | {loc} | {r['face_count']} | {doc_badge} | {r['inference_time_s']}s | {r['short_caption']} |\n")
        f.write("\n---\n\n")
        
        for r in results:
            f.write(f"### {r['media_id']} — `{r['current_path']}`\n\n")
            f.write(f"- **Capture Date**: `{r['capture_timestamp']}`\n")
            f.write(f"- **Location**: {r['city'] or 'N/A'}, {r['country'] or 'N/A'}\n")
            f.write(f"- **Camera**: {r['camera']}\n")
            f.write(f"- **Faces Detected**: {r['face_count']}\n")
            f.write(f"- **Document/Homework Flag**: `{r['is_document_or_homework']}`\n")
            f.write(f"- **Scene Type**: `{r['scene_type']}`\n")
            f.write(f"- **Tags**: `{', '.join(r['tags'])}`\n")
            f.write(f"- **Detailed Description**:\n  > {r['detailed_description']}\n\n")
            
    print(f"Benchmark report saved to:\n  - {json_path}\n  - {md_path}")

if __name__ == '__main__':
    run_benchmark()
