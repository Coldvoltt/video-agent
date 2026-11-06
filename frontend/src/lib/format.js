/** 75.4 -> "1:15", 3725 -> "1:02:05". */
export function timecode(seconds) {
  if (seconds == null || Number.isNaN(seconds)) return '–';
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = String(s % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${sec}` : `${m}:${sec}`;
}

/** Clip lengths: one decimal under a minute ("4.3s"), timecode above. */
export function shortDuration(seconds) {
  if (seconds == null) return '';
  return seconds < 60 ? `${seconds.toFixed(1)}s` : timecode(seconds);
}

/** Tape-label colours, one per source asset, in upload order. */
export const ASSET_COLORS = ['#E7B84A', '#5FB3A8', '#8E9BEA', '#E58E9B', '#B7C46A', '#D99A6C'];

export function assetColorMap(assets = []) {
  return Object.fromEntries(assets.map((a, i) => [a.id, ASSET_COLORS[i % ASSET_COLORS.length]]));
}

const TOOL_LABELS = {
  list_assets: 'Looking at your media',
  get_timeline: 'Checking the timeline',
  get_transcript: 'Reading the transcript',
  search_transcripts: 'Searching what was said',
  find_silences: 'Finding pauses',
  set_timeline: 'Cutting clips',
  set_output_format: 'Changing the format',
  tighten_pacing: 'Tightening the pacing',
  cut_ranges: 'Removing sections',
  set_captions: 'Styling captions',
  set_transitions: 'Adding transitions',
  set_grade: 'Grading colour',
  add_text: 'Adding text',
  add_overlay: 'Placing an overlay',
  add_audio: 'Adding audio',
  remove_layer: 'Removing a layer',
  get_captions: 'Reading the captions',
  edit_captions: 'Editing captions',
  get_reference_style: 'Studying the reference',
  match_reference_color: 'Matching the reference’s colour',
  apply_pacing: 'Matching the pacing',
  apply_reference_style: 'Applying the reference style',
  find_phrase: 'Finding the right moment',
  list_broll: 'Looking through your B-roll',
  search_stock_broll: 'Searching stock footage',
  add_stock_broll: 'Downloading stock footage',
  add_broll: 'Placing B-roll',
  generate_broll_image: 'Generating an image',
  emphasize: 'Adding emphasis',
  add_zoom: 'Adding a zoom',
  add_sound_effect: 'Adding a sound effect',
  auto_sound_effects: 'Adding sound effects',
  set_audio_polish: 'Polishing the audio',
  list_stickers: 'Looking at stickers',
  add_sticker: 'Adding a sticker',
  set_background: 'Changing the background',
};

/** Job messages look like "Working (tighten_pacing)"; show them in plain words. */
export function friendlyProgress(message) {
  const tool = message?.match(/\(([a-z_]+)\)/)?.[1];
  if (tool) return TOOL_LABELS[tool] || 'Working';
  return message || 'Working';
}

export const isActive = (job) => job && (job.status === 'queued' || job.status === 'running');
