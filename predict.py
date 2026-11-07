"""Pokemon prediction using dual ONNX models"""
# Heavy ML dependencies are intentionally NOT imported at module load time.
# They are lazy-loaded in _ensure_heavy_imports() so that simply importing
# this file (e.g. at bot startup) does not co 
# They are released again in unload_models() so RAM returns to startup baseline.
import aiohttp
import io
import os
import json
import time
import hashlib
import asyncio
import gc
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Tuple

# ---------------------------------------------------------------------------
# Lazy-loaded heavy dependencies — populated by _ensure_heavy_imports()
# ---------------------------------------------------------------------------
ort   = None  # onnxruntime
np    = None  # numpy
Image = None  # PIL.Image


def _ensure_heavy_imports():
    """Import RAM-heavy libraries only when models are about to be loaded."""
    global ort, np, Image
    if ort is not None:
        return  # already imported
    import onnxruntime as _ort
    import numpy as _np
    from PIL import Image as _Image
    ort   = _ort
    np    = _np
    Image = _Image


def _release_heavy_imports():
    """
    Drop the module-level reference to onnxruntime.
    numpy and PIL are intentionally LEFT in sys.modules — reimporting them
    after unloading fragments glibc's allocator and causes the
    'NumPy module was reloaded' warning you see in logs.

    We do NOT pop onnxruntime from sys.modules either. The sessions are
    already set to None before this is called, so the model weights are
    freed. Repeatedly unloading/reloading native ML extension modules can
    cause allocator fragmentation; keeping the module object in sys.modules
    costs only a few KB and avoids the instability.
    """
    global ort
    ort = None
    # np and Image are intentionally NOT touched here


# Discord CDN URLs contain rotating query params (?ex=...&hm=...&is=...) that
# change every message even for the same image file.  Strip them so the same
# Pokémon image always hits the prediction cache regardless of URL refresh.
_DISCORD_CDN_RE = re.compile(
    r'^(https?://(?:cdn\.discordapp\.com|media\.discordapp\.net)/[^?#]+)'
)

def _stable_cache_key(url: str) -> str:
    m = _DISCORD_CDN_RE.match(url)
    stable = m.group(1) if m else url
    return hashlib.md5(stable.encode()).hexdigest()

# GitHub raw content URLs for models
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
PRIMARY_REPO_RAW    = "https://raw.githubusercontent.com/cynthiaofpower/myfinalmodel/main"
PRIMARY_REPO_LFS    = "https://media.githubusercontent.com/media/cynthiaofpower/myfinalmodel/main"
SECONDARY_REPO_RAW = "https://raw.githubusercontent.com/cynthiaofpower/myfinalmodel/main"
SECONDARY_REPO_LFS = "https://media.githubusercontent.com/media/cynthiaofpower/myfinalmodel/main"

# .onnx files are stored in Git LFS — must use media URL
# .json files are plain text — use raw URL
PRIMARY_ONNX_URL     = f"{PRIMARY_REPO_LFS}/hallodated.onnx"
PRIMARY_LABELS_URL   = f"{PRIMARY_REPO_RAW}/hallodated.json"
SECONDARY_ONNX_URL   = f"{SECONDARY_REPO_LFS}/halloween.onnx"
SECONDARY_LABELS_URL = f"{SECONDARY_REPO_RAW}/event_labels.json"

CACHE_DIR = os.path.join(os.path.dirname(os.path.realpath(__file__)), "model_cache")
PRIMARY_ONNX_PATH    = os.path.join(CACHE_DIR, "vivillon.onnx")
PRIMARY_LABELS_PATH  = os.path.join(CACHE_DIR, "vivillon.json")
SECONDARY_ONNX_PATH  = os.path.join(CACHE_DIR, "halloween.onnx")
SECONDARY_LABELS_PATH = os.path.join(CACHE_DIR, "event_labels.json")

# -----------------------------------------------------------------------
# Lazy loading toggle
# True  → models are NOT loaded at startup; an admin/owner must run
#         p!model load before predictions work (saves RAM until needed).
# False → models are downloaded/loaded automatically during bot startup.
# -----------------------------------------------------------------------
LAZY_LOAD_MODELS = False

# -----------------------------------------------------------------------
# Confidence thresholds — edit these to tune prediction behaviour
# If primary model confidence >= PRIMARY_CONFIDENCE_THRESHOLD, use it directly.
# Otherwise try secondary model if >= SECONDARY_CONFIDENCE_THRESHOLD.
# If neither meets their threshold, fall back to the primary result anyway.
# -----------------------------------------------------------------------
PRIMARY_CONFIDENCE_THRESHOLD   = 90.0  # %
SECONDARY_CONFIDENCE_THRESHOLD = 95.0  # %

# -----------------------------------------------------------------------
# Pokemon-specific secondary model list
# If the PRIMARY model identifies a Pokemon in this set, ALWAYS also run
# the SECONDARY model and return whichever result has higher confidence.
# Add Pokemon names exactly as they appear in primary model's class labels.
# -----------------------------------------------------------------------
SECONDARY_MODEL_POKEMON = {
    "Meloetta",
}


class PredictionCache:
    """Ultra-lightweight cache - ONLY stores final results"""
    def __init__(self, max_size=100, ttl_seconds=60):  # 100 entries covers ~20s of heavy incense at 5 spawns/s
        self.cache = {}
        self.timestamps = {}
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self._ops_since_gc = 0

    def _cleanup_expired(self):
        current_time = time.time()
        expired_keys = [
            key for key, timestamp in self.timestamps.items()
            if current_time - timestamp > self.ttl_seconds
        ]
        for key in expired_keys:
            self.cache.pop(key, None)
            self.timestamps.pop(key, None)

        # Only GC every 50 ops, not every 20
        self._ops_since_gc += 1
        if self._ops_since_gc >= 50:
            gc.collect()
            self._ops_since_gc = 0

    def get(self, key: str) -> Optional[Tuple[str, str, str]]:
        self._cleanup_expired()
        if key in self.cache:
            current_time = time.time()
            if current_time - self.timestamps[key] <= self.ttl_seconds:
                return self.cache[key]
            else:
                self.cache.pop(key, None)
                self.timestamps.pop(key, None)
        return None

    def set(self, key: str, value: Tuple[str, str, str]):
        self._cleanup_expired()

        if len(self.cache) >= self.max_size:
            sorted_keys = sorted(self.timestamps.items(), key=lambda x: x[1])
            remove_count = max(1, self.max_size // 5)
            for old_key, _ in sorted_keys[:remove_count]:
                self.cache.pop(old_key, None)
                self.timestamps.pop(old_key, None)
            gc.collect()

        self.cache[key] = value
        self.timestamps[key] = time.time()


class ModelDownloader:
    """Handle downloading and caching models from GitHub"""

    @staticmethod
    async def download_file(url: str, dest_path: str, session: aiohttp.ClientSession):
        try:
            timeout = aiohttp.ClientTimeout(total=60, connect=10)

            headers = {}
            if GITHUB_TOKEN:
                headers['Authorization'] = f'token {GITHUB_TOKEN}'

            async with session.get(url, timeout=timeout, headers=headers) as response:
                if response.status == 401:
                    raise ValueError(f"Authentication failed. Check your GITHUB_TOKEN environment variable.")
                if response.status == 404:
                    raise ValueError(f"File not found: {url}. Check repository name and file path.")
                if response.status != 200:
                    raise ValueError(f"HTTP {response.status} error downloading {url}")

                content = await response.read()
                os.makedirs(os.path.dirname(dest_path), exist_ok=True)

                with open(dest_path, 'wb') as f:
                    f.write(content)

                print(f"✅ Downloaded: {os.path.basename(dest_path)}")
                return True
        except Exception as e:
            print(f"❌ Failed to download {url}: {e}")
            return False

    @staticmethod
    async def ensure_models_cached(session: aiohttp.ClientSession):
        os.makedirs(CACHE_DIR, exist_ok=True)

        primary_downloads = [
            (PRIMARY_ONNX_URL,   PRIMARY_ONNX_PATH),
            (PRIMARY_LABELS_URL, PRIMARY_LABELS_PATH),
        ]
        secondary_downloads = [
            (SECONDARY_ONNX_URL,    SECONDARY_ONNX_PATH),
            (SECONDARY_LABELS_URL,  SECONDARY_LABELS_PATH),
        ]

        # Primary downloads are required — raise if any fail
        primary_tasks = []
        for url, path in primary_downloads:
            if not os.path.exists(path):
                print(f"Downloading {os.path.basename(path)}...")
                primary_tasks.append(ModelDownloader.download_file(url, path, session))
            else:
                print(f"✓ Cached: {os.path.basename(path)}")

        if primary_tasks:
            results = await asyncio.gather(*primary_tasks)
            if not all(results):
                raise Exception("Failed to download primary model files")

        # Secondary downloads are optional — log failures but continue
        secondary_tasks = []
        secondary_paths = []
        for url, path in secondary_downloads:
            if not os.path.exists(path):
                print(f"Downloading {os.path.basename(path)}...")
                secondary_tasks.append(ModelDownloader.download_file(url, path, session))
                secondary_paths.append(path)
            else:
                print(f"✓ Cached: {os.path.basename(path)}")

        if secondary_tasks:
            results = await asyncio.gather(*secondary_tasks)
            for ok, path in zip(results, secondary_paths):
                if not ok:
                    print(f"⚠️ Secondary model file unavailable (non-fatal): {os.path.basename(path)}")


class Prediction:
    def __init__(self):
        self.cache = PredictionCache()
        self.primary_session = None
        self.secondary_session = None
        self.primary_class_names = None
        self.secondary_class_names = None
        self.models_initialized = False
        self.allow_auto_load = False
        self._cdn_semaphore = asyncio.Semaphore(8)  # 8 concurrent CDN downloads — enough for 5 spawns/s with headroom
        self._inference_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="onnx")  # dedicated pool, never shared with bot I/O
        self._prediction_counter = 0
        self._loop = None  # cached event loop reference

    async def maybe_eager_load(self, session: aiohttp.ClientSession):
        """Call this once during bot startup.

        If LAZY_LOAD_MODELS is False, this loads the models immediately
        (equivalent to running p!model load). If True, this is a no-op —
        models stay unloaded until an admin/owner runs p!model load.
        """
        if LAZY_LOAD_MODELS:
            print("[INIT] LAZY_LOAD_MODELS is True — skipping eager load. Use p!model load when ready.")
            return
        print("[INIT] LAZY_LOAD_MODELS is False — loading models now at startup...")
        await self.initialize_models(session)

    async def initialize_models(self, session: aiohttp.ClientSession):
        """Download and initialize both models - ONLY ONCE"""
        if self.models_initialized:
            print("[INIT] Models already initialized, skipping...")
            return

        # Import onnxruntime / numpy / Pillow NOW (not at module load time)
        _ensure_heavy_imports()

        print("Initializing prediction models...")
        self._loop = asyncio.get_event_loop()

        await ModelDownloader.ensure_models_cached(session)

        # Load class names
        with open(PRIMARY_LABELS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                sorted_keys = sorted(data.keys(), key=lambda x: int(x))
                self.primary_class_names = [data[k].strip('"') for k in sorted_keys]
            elif isinstance(data, list):
                self.primary_class_names = [name.strip('"') for name in data]
            else:
                raise ValueError("labels_v2.json must be a list or dict")

        # Secondary labels — optional
        try:
            with open(SECONDARY_LABELS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    def _sort_key(x):
                        # Support plain integers ("50016") and alphanumeric ("50197F")
                        try:
                            return (0, int(x), "")
                        except ValueError:
                            # Extract leading digits for numeric prefix sort, rest as string
                            m = re.match(r'^(\d+)(.*)', x)
                            return (0, int(m.group(1)), m.group(2)) if m else (1, 0, x)
                    sorted_keys = sorted(data.keys(), key=_sort_key)
                    self.secondary_class_names = [data[k].strip('"') for k in sorted_keys]
                elif isinstance(data, list):
                    self.secondary_class_names = [name.strip('"') for name in data]
                else:
                    raise ValueError("event_labels.json must be a list or dict")
        except Exception as e:
            print(f"⚠️ Secondary labels unavailable (non-fatal): {e}")
            self.secondary_class_names = None

        providers = ["CPUExecutionProvider"]

        sess_opts = ort.SessionOptions()
        sess_opts.intra_op_num_threads = 1
        sess_opts.inter_op_num_threads = 1
        sess_opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        sess_opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        sess_opts.enable_mem_pattern = False
        sess_opts.enable_cpu_mem_arena = False

        self.primary_session = ort.InferenceSession(
            PRIMARY_ONNX_PATH,
            sess_options=sess_opts,
            providers=providers
        )
        print(f"✅ Primary model initialized: {len(self.primary_class_names)} classes")

        # Secondary model init — optional
        try:
            sess_opts2 = ort.SessionOptions()
            sess_opts2.intra_op_num_threads = 1
            sess_opts2.inter_op_num_threads = 1
            sess_opts2.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            sess_opts2.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            sess_opts2.enable_mem_pattern = False
            sess_opts2.enable_cpu_mem_arena = False

            self.secondary_session = ort.InferenceSession(
                SECONDARY_ONNX_PATH,
                sess_options=sess_opts2,
                providers=providers
            )
            print(f"✅ Secondary model initialized: {len(self.secondary_class_names)} classes")
        except Exception as e:
            print(f"⚠️ Secondary model unavailable (non-fatal): {e}")
            self.secondary_session = None

        self.models_initialized = True
        self.allow_auto_load = True

        gc.collect()

    def unload_models(self):
        """Release ONNX sessions and all model data from memory."""
        self.primary_session = None
        self.secondary_session = None
        self.primary_class_names = None
        self.secondary_class_names = None
        self.models_initialized = False
        self.allow_auto_load = False

        self.cache.cache.clear()
        self.cache.timestamps.clear()

        # Shut down and recreate the inference pool so any in-flight tasks
        # finish cleanly and the threads are released before ORT unloads.
        self._inference_pool.shutdown(wait=True)
        self._inference_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="onnx")

        # Release heavy native libraries so RAM returns to startup baseline.
        # Must be called AFTER sessions are nullified (above) so ORT's own
        # reference count drops to zero before we remove the module.
        _release_heavy_imports()

        print("[UNLOAD] Model sessions and data cleared. Use !loadmodel to reload.")
        gc.collect()

        # Tell glibc to release freed heap pages back to the OS immediately.
        # Without this, Linux keeps the pages in the process address space and
        # RSS stays high even though the memory is logically free.
        import ctypes
        try:
            ctypes.CDLL("libc.so.6").malloc_trim(0)
            print("[UNLOAD] malloc_trim called — heap returned to OS.")
        except Exception as e:
            print(f"[UNLOAD] malloc_trim unavailable: {e}")

    def _generate_cache_key(self, url: str) -> str:
        return _stable_cache_key(url)

    # ------------------------------------------------------------------
    # FIX #1: Fetch raw bytes ONCE, reuse for both model sizes
    # Semaphore now held for the ENTIRE download, not just the pre-sleep.
    # Previously the slot was released before the HTTP request started,
    # meaning all concurrent downloads raced with no real cap.
    # ------------------------------------------------------------------
    async def _fetch_raw_bytes(self, url: str, session: aiohttp.ClientSession, max_retries: int = 1) -> bytes:
        """
        Download image bytes once. max_retries = 1 — at high spawn volume a
        genuinely missing image should be dropped fast, not retried repeatedly
        (2 retries on timeout = up to 20s stall per failed image).
        Semaphore caps concurrent CDN downloads to 8 for the full duration.
        """
        is_discord_cdn = 'cdn.discordapp.com' in url or 'media.discordapp.net' in url

        # Request a 256px thumbnail from Discord's CDN edge servers instead of
        # the full-res image. Our model resizes to 224×224 anyway, so the extra
        # pixels are pure wasted bandwidth. We APPEND &size=256 to preserve the
        # existing auth params (ex=, hm=, is=) — stripping them causes 404s.
        # _stable_cache_key() already strips query params before hashing, so
        # the prediction cache key is unaffected by this change.
        if is_discord_cdn and 'size=' not in url:
            url = f"{url}&size=256" if '?' in url else f"{url}?size=256"

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
        }

        async def _do_fetch() -> bytes:
            for attempt in range(max_retries):
                try:
                    # Tighter timeouts — most Poketwo images are small (fix #3)
                    timeout_total = 8 + (attempt * 4)
                    timeout_connect = 4
                    timeout = aiohttp.ClientTimeout(total=timeout_total, connect=timeout_connect)

                    async with session.get(url, timeout=timeout, headers=headers) as response:
                        if response.status == 429:
                            retry_after = int(response.headers.get('Retry-After', 2))
                            if attempt < max_retries - 1:
                                await asyncio.sleep(retry_after)
                                continue
                            raise ValueError("Rate limited by Discord CDN")

                        if response.status == 404:
                            raise ValueError(f"Image not found (404): {url[:80]}")

                        if response.status in [502, 503, 504]:
                            if attempt < max_retries - 1:
                                await asyncio.sleep(2.0 * (2 ** attempt))
                                continue
                            raise ValueError(f"Server error {response.status}")

                        if response.status != 200:
                            raise ValueError(f"HTTP {response.status} error")

                        data = await response.read()

                    if len(data) < 100:
                        raise ValueError("Invalid/empty image data")

                    return data

                except asyncio.TimeoutError:
                    if attempt < max_retries - 1:
                        await asyncio.sleep(1.0 * (2 ** attempt))
                        continue
                    raise ValueError("Timeout fetching image")

                except aiohttp.ClientError as e:
                    if attempt < max_retries - 1:
                        await asyncio.sleep(1.0 * (2 ** attempt))
                        continue
                    raise ValueError(f"Network error: {e}")

                except ValueError:
                    raise

                except Exception as e:
                    if attempt < max_retries - 1:
                        await asyncio.sleep(0.5 * (2 ** attempt))
                        continue
                    raise ValueError(f"Failed to load image: {e}")

            raise ValueError(f"Failed to load image after {max_retries} attempts")

        # Hold the semaphore for the ENTIRE download so at most 3 CDN
        # requests are in-flight simultaneously.  Non-CDN URLs bypass it.
        if is_discord_cdn:
            async with self._cdn_semaphore:
                return await _do_fetch()
        else:
            return await _do_fetch()

    def _preprocess_from_bytes(self, raw_bytes: bytes, width: int, height: int):
        """
        Resize + normalise from already-downloaded bytes.
        Called twice (primary + secondary) with ZERO extra network I/O.
        Temporary PIL image is explicitly closed and deleted after array
        extraction to release native memory immediately rather than waiting
        for GC — this is one of the main causes of RAM creep under heavy load.
        """
        img = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
        img = img.resize((width, height), Image.BICUBIC)  # faster than LANCZOS, negligible quality diff for CNN
        image_array = np.array(img, dtype=np.float32)
        img.close()
        del img  # release PIL internal buffer immediately

        image_array /= 255.0
        mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
        std  = np.array([0.229, 0.224, 0.225], dtype=np.float32)
        image_array = (image_array - mean) / std
        del mean, std  # free small intermediates
        image_array = np.transpose(image_array, (2, 0, 1))
        image_array = np.expand_dims(image_array, axis=0)
        return image_array

    def softmax(self, x):
        exp_x = np.exp(x - np.max(x))
        return exp_x / np.sum(exp_x)

    # ------------------------------------------------------------------
    # FIX #2: Run blocking ONNX inference in a thread pool executor
    #         so it never blocks the Discord event loop
    # ------------------------------------------------------------------
    def _run_inference(self, session, image,
                       class_names: list) -> Tuple[str, float]:
        """Synchronous inference — called via run_in_executor."""
        inputs = {session.get_inputs()[0].name: image}
        outputs = session.run(None, inputs)
        logits = outputs[0][0]

        pred_idx = int(np.argmax(logits))
        probabilities = self.softmax(logits)
        prob = float(probabilities[pred_idx])
        name = class_names[pred_idx] if pred_idx < len(class_names) else f"unknown_{pred_idx}"

        # Explicitly free numpy arrays — don't rely on GC
        del outputs, logits, probabilities
        return name, prob

    async def predict_with_model(self, image, session,
                                  class_names: list) -> Tuple[str, float]:
        """Async wrapper: offloads blocking inference to dedicated ONNX thread pool."""
        loop = self._loop or asyncio.get_event_loop()
        name, prob = await loop.run_in_executor(
            self._inference_pool, self._run_inference, session, image, class_names
        )
        return name, prob

    # ------------------------------------------------------------------
    # Core predict — single download, dual resize (fix #1)
    # ------------------------------------------------------------------
    async def predict(self, url: str, session: aiohttp.ClientSession = None) -> Tuple[str, str]:
        """
        Run prediction.
        - Downloads image bytes ONCE regardless of which model(s) are used.
        - ONNX inference runs off the event loop via run_in_executor.
        - Raises RuntimeError if models are not loaded.
        """
        # Use stable key — Discord CDN rotates ?ex=/hm= but path is permanent
        cache_key = _stable_cache_key(url)
        cached_result = self.cache.get(cache_key)
        if cached_result:
            return cached_result[0], cached_result[1]

        if not self.models_initialized:
            raise RuntimeError(
                "Prediction models are not loaded. "
                "Use `p!model load` to load them before running predictions."
            )

        if session is None:
            import __main__
            session = getattr(__main__, 'http_session', None)
            if session is None:
                raise ValueError("HTTP session not available")

        # ----- Single download (fix #1) --------------------------------
        raw_bytes = await self._fetch_raw_bytes(url, session)

        try:
            loop = self._loop or asyncio.get_event_loop()
            primary_image = self._preprocess_from_bytes(raw_bytes, 224, 224)

            secondary_available = (
                self.secondary_session is not None and
                self.secondary_class_names is not None
            )

            # ── Step 1: always run primary first ────────────────────────────────
            primary_name, primary_prob = await loop.run_in_executor(
                self._inference_pool, self._run_inference, self.primary_session, primary_image, self.primary_class_names
            )
            del primary_image
            primary_confidence_pct = primary_prob * 100

            # ── Step 2: decide whether secondary is needed ───────────────────────
            # Secondary runs only when:
            #   (a) it is available, AND
            #   (b) primary is below threshold OR the pokemon is in SECONDARY_MODEL_POKEMON
            # For the ~94%+ confident majority this saves an entire inference call.
            needs_secondary = secondary_available and (
                primary_confidence_pct < PRIMARY_CONFIDENCE_THRESHOLD
                or primary_name in SECONDARY_MODEL_POKEMON
            )

            if needs_secondary:
                secondary_image = self._preprocess_from_bytes(raw_bytes, 224, 224)
                secondary_name, secondary_prob = await loop.run_in_executor(
                    self._inference_pool, self._run_inference, self.secondary_session, secondary_image, self.secondary_class_names
                )
                del secondary_image
                secondary_confidence_pct = secondary_prob * 100

                # ── Pokemon-specific override ────────────────────────────────────
                if primary_name in SECONDARY_MODEL_POKEMON:
                    if secondary_confidence_pct >= primary_confidence_pct:
                        confidence = f"{secondary_confidence_pct:.2f}%"
                        self.cache.set(cache_key, (secondary_name, confidence, "secondary_override"))
                        self._maybe_gc()
                        return secondary_name, confidence
                    else:
                        confidence = f"{primary_confidence_pct:.2f}%"
                        self.cache.set(cache_key, (primary_name, confidence, "primary_override"))
                        self._maybe_gc()
                        return primary_name, confidence

                # ── Normal threshold logic (primary was low-confidence) ──────────
                if secondary_confidence_pct >= SECONDARY_CONFIDENCE_THRESHOLD:
                    confidence = f"{secondary_confidence_pct:.2f}%"
                    self.cache.set(cache_key, (secondary_name, confidence, "secondary"))
                    self._maybe_gc()
                    return secondary_name, confidence

                # Both below threshold — fall back to primary
                confidence = f"{primary_confidence_pct:.2f}%"
                self.cache.set(cache_key, (primary_name, confidence, "primary_fallback"))
                self._maybe_gc()
                return primary_name, confidence

            else:
                # Primary was confident enough (or secondary unavailable) — skip secondary entirely
                confidence = f"{primary_confidence_pct:.2f}%"
                label = "primary" if primary_confidence_pct >= PRIMARY_CONFIDENCE_THRESHOLD else "primary_only"
                self.cache.set(cache_key, (primary_name, confidence, label))
                self._maybe_gc()
                return primary_name, confidence

        finally:
            # raw_bytes is the only large allocation; release it ASAP
            del raw_bytes

    # ------------------------------------------------------------------
    # FIX #4: GC only every 50 predictions, not every 10
    # ------------------------------------------------------------------
    def _maybe_gc(self):
        self._prediction_counter += 1
        if self._prediction_counter >= 50:
            gc.collect()
            self._prediction_counter = 0


def main():
    """Test function for development"""

    async def test_predict():
        predictor = Prediction()

        async with aiohttp.ClientSession() as session:
            await predictor.initialize_models(session)

            while True:
                url = input("Enter Pokémon image URL (or 'q' to quit): ").strip()
                if url.lower() == 'q':
                    break

                try:
                    name, confidence = await predictor.predict(url, session)
                    print(f"Predicted Pokémon: {name} (confidence: {confidence})")
                except Exception as e:
                    print(f"Error: {e}")

    asyncio.run(test_predict())


if __name__ == "__main__":
    main()
