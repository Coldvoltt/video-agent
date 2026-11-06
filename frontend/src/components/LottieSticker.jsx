import { useEffect, useRef, useState } from 'react';

// lottie-web is ~300 KB: load it the first time a sticker is shown, not with the app.
let lottiePromise = null;
const loadLottie = () => {
  lottiePromise ||= import('lottie-web/build/player/lottie_svg').then((m) => m.default);
  return lottiePromise;
};

/**
 * A Lottie animation driven by an external clock (seconds since the sticker appeared),
 * so the live preview shows exactly the frame the render will. With `autoplay` it simply
 * plays (used for thumbnails in the sticker picker).
 */
export default function LottieSticker({ url, elapsed = 0, loop = false, autoplay = false, className, style, title }) {
  const box = useRef(null);
  const anim = useRef(null);
  const [ready, setReady] = useState(0);

  useEffect(() => {
    if (!box.current || !url) return undefined;
    let cancelled = false;
    let a = null;
    loadLottie().then((lottie) => {
      if (cancelled || !box.current) return;
      a = lottie.loadAnimation({ container: box.current, renderer: 'svg', loop: autoplay ? true : loop, autoplay, path: url });
      anim.current = a;
      setReady((n) => n + 1);
    });
    return () => {
      cancelled = true;
      a?.destroy();
      anim.current = null;
    };
  }, [url, loop, autoplay]);

  useEffect(() => {
    const a = anim.current;
    if (!a || autoplay) return;
    const seek = () => {
      const total = (a.totalFrames / (a.frameRate || 30)) * 1000;
      if (!total) return;
      const ms = elapsed * 1000;
      a.goToAndStop(loop ? ms % total : Math.min(ms, total - 1), false);
    };
    if (a.isLoaded) {
      seek();
      return undefined;
    }
    a.addEventListener('DOMLoaded', seek);
    // The load effect's cleanup may already have destroyed the animation (it runs first on
    // unmount); lottie-web throws if you remove a listener from a destroyed animation.
    return () => {
      if (anim.current === a) a.removeEventListener('DOMLoaded', seek);
    };
  }, [elapsed, loop, autoplay, ready]);

  return <div ref={box} className={className} style={style} title={title} aria-hidden="true" />;
}
