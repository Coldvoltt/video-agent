// Caption and text fonts: the same files the renderer bundles (backend/editor/typefaces.py), loaded here from
// Google Fonts. Sizes need no adjusting: the renderer scales each font by its metrics so it draws as big as
// the default would, which is exactly what CSS does when only the family changes.
export const FONTS = {
  montserrat: { label: 'Montserrat', css: "'Montserrat', sans-serif", weight: 800, feel: 'Clean and bold' },
  anton: { label: 'Anton', css: "'Anton', sans-serif", weight: 400, feel: 'Tall and condensed' },
  bebas: { label: 'Bebas Neue', css: "'Bebas Neue', sans-serif", weight: 400, feel: 'Cinematic capitals' },
  archivo_black: { label: 'Archivo Black', css: "'Archivo Black', sans-serif", weight: 400, feel: 'Heavy and loud' },
  poppins: { label: 'Poppins', css: "'Poppins', sans-serif", weight: 800, feel: 'Rounded and friendly' },
  bangers: { label: 'Bangers', css: "'Bangers', cursive", weight: 400, feel: 'Comic book' },
  luckiest_guy: { label: 'Luckiest Guy', css: "'Luckiest Guy', cursive", weight: 400, feel: 'Chunky and bubbly' },
  permanent_marker: { label: 'Permanent Marker', css: "'Permanent Marker', cursive", weight: 400, feel: 'Hand-drawn' },
  pacifico: { label: 'Pacifico', css: "'Pacifico', cursive", weight: 400, feel: 'Retro script' },
  dm_serif: { label: 'DM Serif Italic', css: "'DM Serif Display', serif", weight: 400, style: 'italic', feel: 'Elegant serif' },
};

export const fontStyle = (id) => {
  const f = FONTS[id];
  return f ? { fontFamily: f.css, fontWeight: f.weight, fontStyle: f.style || 'normal' } : {};
};
