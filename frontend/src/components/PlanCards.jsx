// Plan cards, shared by the landing page and the dashboard. Prices and coin amounts come from the server.

const CURRENCY = { USD: (n) => `$${n}`, NGN: (n) => `₦${Number(n).toLocaleString('en-NG')}` };

export function price(plan, currency) {
  return CURRENCY[currency](plan.price?.[currency] || 0);
}

export function features(plan, plans) {
  const byId = Object.fromEntries(plans.map((p) => [p.id, p]));
  const angles = `${plan.max_angles === 1 ? 'One camera angle' : `Up to ${plan.max_angles} camera angles`}`;
  if (plan.id === 'free') {
    return [
      `${plan.coins} vid-coins a month`,
      angles,
      `${plan.max_broll} B-roll cutaway per project`,
      'Karaoke and minimal captions',
      'Chat editing, cuts, colour, sound, stickers and effects',
    ];
  }
  if (plan.id === 'basic') {
    return [
      `${plan.coins} vid-coins for a month`,
      angles,
      'Unlimited B-roll',
      'Every caption style, 10 fonts and styled key words',
      `Everything in ${byId.free?.name || 'Free'}`,
    ];
  }
  return [
    `${plan.coins} vid-coins for ${plan.months} months`,
    angles,
    'AI background removal',
    'AI-generated B-roll images',
    `Everything in ${byId.basic?.name || 'Basic'}`,
  ];
}

const TONE = { free: 'paper', basic: 'yellow', pro: 'pink' };

/**
 * The three plans. `current` marks the person's plan; `onChoose(planId)` runs for the paid ones
 * (free links to the editor). `variant` = 'landing' or 'app' for the two colour schemes.
 */
export default function PlanCards({ plans, currency, current, onChoose, variant = 'app', busy }) {
  return (
    <ul className={`plan-cards plan-cards-${variant}`}>
      {plans.map((p) => {
        const isCurrent = current === p.id;
        const below = current && ['free', 'basic', 'pro'].indexOf(p.id) < ['free', 'basic', 'pro'].indexOf(current);
        return (
          <li key={p.id} className={`plan-card tone-${TONE[p.id]} ${p.id === 'basic' ? 'is-featured' : ''}`}>
            <div className="plan-top">
              <h3 className="plan-name">{p.name}</h3>
              {p.id === 'basic' && <span className="plan-flag">Most popular</span>}
            </div>
            <p className="plan-price">
              <span className="plan-amount">{price(p, currency)}</span>
              <span className="plan-per">{!p.price?.USD ? 'every month' : p.months === 1 ? 'a month' : `for ${p.months} months`}</span>
            </p>
            <p className="plan-coins">
              <span className="coin" aria-hidden="true">V</span>
              {p.coins} vid-coins
            </p>
            <ul className="plan-features">
              {features(p, plans).map((f) => (
                <li key={f}>{f}</li>
              ))}
            </ul>
            {p.id === 'free' ? (
              isCurrent ? <span className="plan-current">Your plan</span>
                : <a className="plan-cta plan-cta-quiet" href="/edit">{current ? 'Included' : 'Start free'}</a>
            ) : (
              <button className="plan-cta" disabled={busy || below} onClick={() => onChoose(p.id)}>
                {isCurrent ? `Buy more ${p.name}` : below ? 'You have a bigger plan' : `Upgrade to ${p.name}`}
              </button>
            )}
          </li>
        );
      })}
    </ul>
  );
}
