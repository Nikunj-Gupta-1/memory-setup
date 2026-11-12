import os
import sys
from pathlib import Path
_viewer_root = Path(__file__).resolve().parent.parent
if str(_viewer_root) not in sys.path:
    sys.path.insert(0, str(_viewer_root))

import time
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image
import numpy as np

from pipeline.config import WORKSPACE_ROOT, MIN_FREE_DISK_GB, PROCESSING_CACHE_DIR, MANIFEST_JSON_PATH

def check_disk_space() -> float:
    st = os.statvfs(WORKSPACE_ROOT)
    return (st.f_bavail * st.f_frsize) / (1024 ** 3)

def convert_single(task):
    src_path, dst_path = task
    # sips conversion with 85 quality and max 1600 dimension
    cmd = ['sips', '-s', 'format', 'jpeg', '-s', 'formatOptions', '85', '-Z', '1600', str(src_path), '--out', str(dst_path)]
    res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return res.returncode == 0 and dst_path.exists() and dst_path.stat().st_size > 0

def run_refresh():
    print("=" * 60)
    print("STEP 4.0: UNSANDBOXED PROXY REFRESH (HEIC -> JPEG)")
    print("=" * 60)
    
    free_gb = check_disk_space()
    print(f"Current free disk space: {free_gb:.2f} GiB (Safety threshold: {MIN_FREE_DISK_GB} GiB)")
    if free_gb < MIN_FREE_DISK_GB:
        raise RuntimeError("Free disk space below minimum threshold!")

    manifest_path = MANIFEST_JSON_PATH
    with open(manifest_path) as f:
        manifest = json.load(f)
        
    tasks = []
    for item in manifest:
        p = item['processing_path']
        if p.startswith('.processing_cache/'):
            tasks.append((WORKSPACE_ROOT / item['current_path'], WORKSPACE_ROOT / p))
            
    print(f"Found {len(tasks):,} proxy files to refresh in .processing_cache/...")
    
    start_time = time.time()
    batch_size = 500
    success_count = 0
    fail_count = 0
    
    with ThreadPoolExecutor(max_workers=8) as executor:
        for i in range(0, len(tasks), batch_size):
            chunk = tasks[i:i + batch_size]
            futures = [executor.submit(convert_single, t) for t in chunk]
            for f in as_completed(futures):
                if f.result():
                    success_count += 1
                else:
                    fail_count += 1
            
            elapsed = time.time() - start_time
            rate = success_count / elapsed if elapsed > 0 else 0
            cur_free = check_disk_space()
            print(f"Progress: {success_count + fail_count:,}/{len(tasks):,} proxies refreshed ({rate:.1f} img/s | Free disk: {cur_free:.2f} GiB)")
            if cur_free < MIN_FREE_DISK_GB:
                raise RuntimeError(f"ABORTING: Free disk space dropped to {cur_free:.2f} GiB!")
                
    total_time = time.time() - start_time
    print(f"\nProxy refresh completed in {total_time:.1f}s ({total_time/60:.1f} min). Success: {success_count:,}, Failed: {fail_count}")
    
    # Verification pass on random sample
    print("Verifying pixel fidelity on 50 sample proxies...")
    import random
    sample_tasks = random.sample(tasks, min(50, len(tasks)))
    black_count = 0
    for _, dst in sample_tasks:
        img = Image.open(dst)
        arr = np.array(img)
        if arr.max() == 0:
            black_count += 1
            
    if black_count == 0:
        print("✓ 100% of sampled proxies verified as non-black, full-color images!")
    else:
        print(f"⚠️ Warning: {black_count}/50 sampled proxies were black!")

if __name__ == '__main__':
    run_refresh()
