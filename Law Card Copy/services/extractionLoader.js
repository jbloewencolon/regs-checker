/**
 * Lazy loader for per-jurisdiction extraction chunks.
 *
 * Each jurisdiction's extractions live in a separate JSON file. Two sources:
 *   1. Supabase Storage  snapshots/extractions/{CODE}.json   (refreshed online via
 *      the generate-extraction-chunks Edge Function — no redeploy needed)
 *   2. Bundled static    public/data/extractions/{CODE}.json (build-time fallback)
 *
 * Storage wins when its manifest.generatedAt is newer than the bundled manifest,
 * mirroring the snapshot-from-Storage pattern in api.js. Falls back to static when
 * Storage is unavailable (e.g. no session) or older.
 *
 * Usage:
 *   import { loadExtractionChunk, getExtractionsForLaw } from './extractionLoader';
 *   const lawData = await getExtractionsForLaw(37, 'CA');
 *   // lawData.obligations, lawData.thresholds, ...
 */

import { getSupabase } from './supabase';

const BASE_PATH = import.meta.env.BASE_URL + 'data/extractions';
const STORAGE_BUCKET = 'snapshots';
const STORAGE_PREFIX = 'extractions';

const chunkCache = new Map();
let _sourcePromise = null; // resolves to 'storage' | 'static'

// ── Low-level fetchers ────────────────────────────────────────────────────────

async function downloadStorageJson(file) {
  try {
    const supabase = await getSupabase();
    if (!supabase) return null;
    const { data: blob, error } = await supabase.storage
      .from(STORAGE_BUCKET)
      .download(`${STORAGE_PREFIX}/${file}`);
    if (error || !blob) return null;
    return JSON.parse(await blob.text());
  } catch {
    return null;
  }
}

async function fetchStaticJson(file) {
  try {
    const resp = await fetch(`${BASE_PATH}/${file}`);
    if (!resp.ok) return null;
    return await resp.json();
  } catch {
    return null;
  }
}

/**
 * Resolve once whether Storage chunks are fresher than the bundled set.
 * Compares manifest.generatedAt on each side. Cached for the session.
 */
function resolveSource() {
  if (!_sourcePromise) {
    _sourcePromise = (async () => {
      const [storageMan, staticMan] = await Promise.all([
        downloadStorageJson('manifest.json'),
        fetchStaticJson('manifest.json'),
      ]);
      const storageTs = storageMan?.generatedAt ? new Date(storageMan.generatedAt).getTime() : 0;
      const staticTs = staticMan?.generatedAt ? new Date(staticMan.generatedAt).getTime() : 0;
      return storageTs > 0 && storageTs >= staticTs ? 'storage' : 'static';
    })();
  }
  return _sourcePromise;
}

// ── Public API ────────────────────────────────────────────────────────────────

/**
 * Load the extraction chunk for a jurisdiction code.
 * Returns { jurisdiction, code, lawCount, extractionCount, byLaw } or null.
 * Prefers Storage when fresher; falls back to bundled static chunk.
 */
export async function loadExtractionChunk(jurisdictionCode) {
  if (!jurisdictionCode) return null;
  const code = jurisdictionCode.toUpperCase();
  if (chunkCache.has(code)) return chunkCache.get(code);

  const source = await resolveSource();
  let chunk = source === 'storage' ? await downloadStorageJson(`${code}.json`) : null;
  // Fall back to the bundled chunk if Storage is the source but this jurisdiction
  // isn't present there (e.g. partial upload), or if the source is static.
  if (!chunk) chunk = await fetchStaticJson(`${code}.json`);

  chunkCache.set(code, chunk); // may be null — jurisdiction has no extractions
  return chunk;
}

/**
 * Get extraction data for a specific law.
 * Returns { obligations, thresholds, definitions, ambiguities, ... } or null.
 */
export async function getExtractionsForLaw(lawId, jurisdictionCode) {
  const chunk = await loadExtractionChunk(jurisdictionCode);
  if (!chunk || !chunk.byLaw) return null;
  return chunk.byLaw[String(lawId)] || null;
}

/**
 * Preload the manifest (Storage-preferred) to check which jurisdictions have data.
 */
export async function loadExtractionManifest() {
  const source = await resolveSource();
  if (source === 'storage') {
    const storageMan = await downloadStorageJson('manifest.json');
    if (storageMan) return storageMan;
  }
  return fetchStaticJson('manifest.json');
}

/**
 * Clear the chunk cache and re-resolve the source.
 * Call after an online chunk refresh so the app picks up the new Storage chunks.
 */
export function clearExtractionCache() {
  chunkCache.clear();
  _sourcePromise = null;
}
