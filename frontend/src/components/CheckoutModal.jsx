import { useEffect, useRef, useState } from 'react';
import { api, errorMessage } from '../lib/api';
import { price } from './PlanCards';

/**
 * Demo checkout: looks like a card payment but no money moves. The server records the purchase and
 * unlocks the plan; a real provider (Paystack) will replace the confirm step with a verified payment.
 */
export default function CheckoutModal({ plan, initialCurrency = 'USD', onClose, onPaid }) {
  const [currency, setCurrency] = useState(initialCurrency);
  const [step, setStep] = useState('form'); // form | paying | done | failed
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');
  const dialog = useRef(null);

  useEffect(() => {
    const d = dialog.current;
    if (d && !d.open) d.showModal();
    return () => d?.open && d.close();
  }, []);

  const pay = async (success) => {
    setError('');
    setStep('paying');
    try {
      const co = await api.checkout(plan.id, currency);
      await new Promise((r) => setTimeout(r, 900)); // the moment a real card check takes
      const r = await api.confirmDemo(co.reference, success);
      if (r.status === 'paid') {
        setResult(r);
        setStep('done');
        onPaid?.(r);
      } else {
        setStep('failed');
      }
    } catch (e) {
      setError(errorMessage(e));
      setStep('form');
    }
  };

  const until = result?.period_end
    ? new Date(result.period_end).toLocaleDateString(undefined, { day: 'numeric', month: 'long', year: 'numeric' })
    : '';

  return (
    <dialog ref={dialog} className="auth-modal checkout" aria-labelledby="checkout-title" onCancel={onClose}>
      <div className="auth-form">
        <div className="auth-head">
          <span className="demo-badge">Demo payment: no money is taken</span>
          <button type="button" className="button-quiet auth-close" onClick={onClose} aria-label="Close">✕</button>
        </div>

        {step === 'done' ? (
          <>
            <h2 id="checkout-title">You’re on {result.plan_name}</h2>
            <p className="auth-reason">
              You have <strong>{result.coins} vid-coins</strong>
              {until ? `, and ${result.plan_name} lasts until ${until}` : ''}. Everything in the plan is unlocked.
            </p>
            <button className="button-primary auth-submit" onClick={onClose}>Back to editing</button>
          </>
        ) : (
          <>
            <h2 id="checkout-title">Upgrade to {plan.name}</h2>
            <p className="auth-reason">
              {plan.coins} vid-coins and everything in {plan.name}, for {plan.months === 1 ? 'a month' : `${plan.months} months`}.
            </p>
            <div className="auth-switch" role="radiogroup" aria-label="Currency">
              {['USD', 'NGN'].map((c) => (
                <button key={c} type="button" role="radio" aria-checked={currency === c} aria-selected={currency === c}
                  onClick={() => setCurrency(c)} disabled={step === 'paying'}>
                  {c === 'USD' ? 'US dollars' : 'Naira'}
                </button>
              ))}
            </div>
            <div className="checkout-total">
              <span>Total</span>
              <strong>{price(plan, currency)}</strong>
            </div>
            <fieldset className="checkout-card" disabled>
              <legend className="control-label">Card (test details, nothing is charged)</legend>
              <input value="4084 0840 8408 4081" readOnly aria-label="Card number" />
              <div className="checkout-row">
                <input value="12 / 30" readOnly aria-label="Expiry" />
                <input value="408" readOnly aria-label="Security code" />
              </div>
            </fieldset>
            {step === 'failed' && <p className="auth-error" role="alert">The card was declined (demo). Try again.</p>}
            {error && <p className="auth-error" role="alert">{error}</p>}
            <button className="button-primary auth-submit" disabled={step === 'paying'} onClick={() => pay(true)}>
              {step === 'paying' ? 'Processing…' : `Pay ${price(plan, currency)}`}
            </button>
            <button type="button" className="link-button checkout-decline" disabled={step === 'paying'} onClick={() => pay(false)}>
              Try a declined card instead
            </button>
          </>
        )}
      </div>
    </dialog>
  );
}
