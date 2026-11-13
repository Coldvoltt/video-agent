import { useEffect, useState } from 'react';
import { api } from './lib/api';
import PlanCards from './components/PlanCards';
import './landing.css';

// The headline plays like one of the editor's karaoke captions: each word lights up as it's "said",
// and the important words switch into their own caption font, the way emphasis works in the product.
const HEADLINE = [
  { w: 'Say' }, { w: 'what' }, { w: 'you' }, { w: 'want.', emp: 'want' },
  { br: true },
  { w: 'Get' }, { w: 'the' }, { w: 'edit', emp: 'edit' }, { w: 'back.' },
];
const STEP_MS = 360;

const LANES = [
  { lane: 'Captions', tone: 'yellow', width: 92, ask: '“Karaoke captions, and make the important words pop in a comic font.”',
    does: 'Word-by-word captions from your transcript, in ten fonts, with key words in their own font and colour.' },
  { lane: 'B-roll', tone: 'blue', width: 64, ask: '“Add B-roll that shows what I’m talking about.”',
    does: 'Reads what you say, searches stock footage with its own keywords and only places clips that clearly match.' },
  { lane: 'Multicam', tone: 'mint', width: 78, ask: '“These are three angles of the same take. Cut between them.”',
    does: 'Lines the cameras up by their sound, picks the clearest audio and switches angles at natural pauses.' },
  { lane: 'Pacing', tone: 'tangerine', width: 52, ask: '“Cut the ums and the long pauses.”',
    does: 'Tightens the edit from your transcript, without chopping words in half.' },
  { lane: 'Background', tone: 'pink', width: 70, ask: '“Put me in front of a sunset instead of my bedroom.”',
    does: 'Cuts you out with AI or keys out a green screen, then blurs, recolours or replaces what’s behind you.' },
  { lane: 'Stickers', tone: 'yellow', width: 38, ask: '“An arrow when I point at the elephants.”',
    does: 'Animated callouts and emoji, timed to the words you say.' },
  { lane: 'Look', tone: 'blue', width: 58, ask: '“Edit it like this reference video.”',
    does: 'Copies the pacing, captions, colour and format of a video whose style you like.' },
  { lane: 'Effects', tone: 'mint', width: 44, ask: '“It’s grainy. Clean it up, then blur the number plate.”',
    does: 'Video noise removal, film grain and blur, on the whole frame or just part of it.' },
];

const STEPS = [
  ['Add your footage', 'Upload clips from your phone or camera, or paste a link to a YouTube video you own.'],
  ['Ask for the edit', 'Describe it the way you’d brief an editor: the length, the format, the vibe.'],
  ['Fix anything by hand', 'Trim clips, reword captions, move stickers. Every change is a version you can undo.'],
  ['Export', 'Download a full-quality video, sized for Shorts, Reels, TikTok or YouTube.'],
];

function KaraokeHeadline() {
  const words = HEADLINE.filter((x) => !x.br);
  const reduced = typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  const [spoken, setSpoken] = useState(reduced ? words.length + 1 : 0);

  useEffect(() => {
    if (spoken > words.length) return undefined;
    const t = setTimeout(() => setSpoken((n) => n + 1), spoken === 0 ? 500 : STEP_MS);
    return () => clearTimeout(t);
  }, [spoken, words.length]);

  let i = -1;
  return (
    <h1 className="lp-headline" aria-label="Say what you want. Get the edit back.">
      {HEADLINE.map((x, k) => {
        if (x.br) return <br key={k} />;
        i += 1;
        const state = i < spoken - 1 ? 'is-said' : i === spoken - 1 ? 'is-now' : 'is-coming';
        // the space sits outside the inline-block word, or it would be dropped
        return (
          <span key={k} aria-hidden="true">
            <span className={`lp-word ${state} ${x.emp ? `lp-emp lp-emp-${x.emp}` : ''}`}>{x.w}</span>{' '}
          </span>
        );
      })}
    </h1>
  );
}

function HeroPhone() {
  return (
    <figure className="lp-phone" aria-label="The editor on a phone: a request, the reply and the edited video">
      <div className="lp-phone-screen">
        <div className="lp-chat-head">
          <span className="lp-chat-avatar" aria-hidden="true">V</span>
          <span className="lp-chat-who">
            <strong>VidAgent</strong>
            <span>Editing Zoo day.mp4</span>
          </span>
        </div>
        <div className="lp-thread">
          <p className="lp-msg lp-msg-you">
            Make it a vertical short with karaoke captions. Arrow when I mention the elephants.
          </p>
          <div className="lp-msg lp-msg-editor">
            <p>Done: 9:16, captions on, an arrow from 0:03 to 0:06. It’s 0:19 long.</p>
            <div className="lp-clip" aria-hidden="true">
              <span className="lp-clip-scene" />
              <svg className="lp-clip-arrow" viewBox="0 0 120 60">
                <path d="M6 48 C 40 46, 70 34, 100 14" />
                <path d="M84 10 L 102 13 L 96 30" />
              </svg>
              <span className="lp-clip-caption">
                we saw the <b>ELEPHANTS</b>
              </span>
              <span className="lp-clip-time">0:04 / 0:19</span>
            </div>
            <div className="lp-mini-lanes" aria-hidden="true">
              {[['blue', 40, 18], ['yellow', 0, 100], ['pink', 16, 16]].map(([tone, left, width]) => (
                <span key={tone} className="lp-mini-track">
                  <span className={`lp-mini-bar tone-${tone}`} style={{ left: `${left}%`, width: `${width}%` }} />
                </span>
              ))}
            </div>
          </div>
          <p className="lp-msg lp-msg-you">Now make the important words pop in a comic font.</p>
          <p className="lp-msg lp-msg-editor lp-typing" aria-label="The editor is working">
            <span /><span /><span />
          </p>
        </div>
        <div className="lp-composer" aria-hidden="true">
          <span>Ask for an edit…</span>
          <span className="lp-send">↑</span>
        </div>
      </div>
    </figure>
  );
}

// Smooth scroll to a section (a plain jump for people who prefer reduced motion).
const scrollTo = (id) => (e) => {
  const target = document.getElementById(id);
  if (!target) return;
  e.preventDefault();
  const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  target.scrollIntoView({ behavior: reduced ? 'auto' : 'smooth', block: 'start' });
  history.replaceState(null, '', `#${id}`);
};
const scrollToAsks = scrollTo('asks');

function Pricing() {
  const [plans, setPlans] = useState(null);
  const [currency, setCurrency] = useState('USD');
  useEffect(() => {
    api.plans().then((r) => setPlans(r.plans)).catch(() => setPlans([]));
  }, []);
  if (plans && !plans.length) return null;
  return (
    <section className="lp-paper lp-pricing" id="pricing" aria-labelledby="pricing-title">
      <div className="lp-pricing-head">
        <h2 id="pricing-title" className="lp-h2">Pricing</h2>
        <p className="lp-section-lede">
          Edit, preview and chat as much as you like. A vid-coin is spent only when you export a finished video:
          one coin per started minute.
        </p>
        <div className="currency-toggle lp-currency" role="radiogroup" aria-label="Show prices in">
          {['USD', 'NGN'].map((c) => (
            <button key={c} role="radio" aria-checked={currency === c} className={currency === c ? 'is-on' : ''}
              onClick={() => setCurrency(c)}>
              {c === 'USD' ? 'USD $' : 'Naira ₦'}
            </button>
          ))}
        </div>
      </div>
      {plans ? (
        <PlanCards plans={plans} currency={currency} variant="landing"
          onChoose={(id) => { window.location.href = `/dashboard?upgrade=${id}&currency=${currency}#plan`; }} />
      ) : (
        <p className="lp-section-lede">Loading plans…</p>
      )}
    </section>
  );
}

export default function Landing() {
  const [signedIn, setSignedIn] = useState(false);
  useEffect(() => {
    api.me().then((r) => setSignedIn(!!r.user)).catch(() => {});
  }, []);
  return (
    <div className="landing">
      <header className="lp-nav">
        <a className="lp-brand" href="/">
          <span className="lp-brand-mark" aria-hidden="true">V</span>
          VidAgent
        </a>
        <nav className="lp-nav-links" aria-label="Main">
          <a className="lp-nav-link" href="#asks" onClick={scrollToAsks}>Use cases</a>
          <a className="lp-nav-link" href="#pricing" onClick={scrollTo('pricing')}>Pricing</a>
          <a className="lp-nav-link" href="/dashboard">{signedIn ? 'Dashboard' : 'Sign in'}</a>
          <a className="lp-button lp-button-small" href="/edit">Open the editor</a>
        </nav>
      </header>

      <section className="lp-hero">
        <div className="lp-hero-copy">
          <KaraokeHeadline />
          <p className="lp-lede">
            VidAgent is a video editor you talk to. Upload your footage, say how it should look, and it cuts,
            captions, colours and adds B-roll for you. Then change anything by hand.
          </p>
          <div className="lp-actions">
            <a className="lp-button" href="/edit">Start a project</a>
            <a className="lp-link" href="#asks" onClick={scrollToAsks}>See what you can ask for</a>
          </div>
        </div>
        <HeroPhone />
      </section>

      <section className="lp-paper lp-asks" id="asks" aria-labelledby="asks-title">
        <div className="lp-asks-head">
          <h2 id="asks-title" className="lp-h2">What you can ask for</h2>
          <p className="lp-section-lede">
            Talk to it like you’d brief an editor. Every request lands on the timeline as its own layer, so you can
            see what changed and adjust it.
          </p>
          <ul className="lp-legend" aria-label="Timeline layers">
            {LANES.map((l) => (
              <li key={l.lane}>
                <span className={`lp-dot tone-${l.tone}`} aria-hidden="true" />
                {l.lane}
              </li>
            ))}
          </ul>
        </div>
        <ol className="lp-convo">
          {LANES.map((l) => (
            <li key={l.lane} className="lp-exchange">
              <p className={`lp-ask tone-${l.tone}`}>{l.ask.replace(/[“”]/g, '')}</p>
              <div className="lp-answer">
                <span className={`lp-tag tone-${l.tone}`}>{l.lane}</span>
                <p>{l.does}</p>
              </div>
            </li>
          ))}
        </ol>
      </section>

      <section className="lp-paper lp-steps-wrap" aria-labelledby="steps-title">
        <h2 id="steps-title" className="lp-h2">From raw clip to post</h2>
        <ol className="lp-steps">
          {STEPS.map(([title, body], i) => (
            <li key={title} className="lp-step">
              <span className={`lp-step-num tone-${['yellow', 'pink', 'blue', 'mint'][i]}`} aria-hidden="true">{i + 1}</span>
              <h3 className="lp-h3">{title}</h3>
              <p>{body}</p>
            </li>
          ))}
        </ol>
      </section>

      <Pricing />

      <section className="lp-close">
        <h2 className="lp-close-title">Bring the raw clip. Leave with the post.</h2>
        <a className="lp-button" href="/edit">Start a project</a>
      </section>

      <footer className="lp-footer">
        <p>
          Stock footage from Pixabay. Animated emoji from Noto Animated Emoji (CC BY 4.0). Background removal by
          MODNet. Fonts from Google Fonts. Only edit videos you own or have the rights to.
        </p>
      </footer>
    </div>
  );
}
