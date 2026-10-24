// "Add to Home Screen" helper. Android/Chrome: a real Install button (beforeinstallprompt). iPhone has no such
// API, so it shows the two taps instead. Hidden once the app runs from the home screen, or after "Not now".
import { useEffect, useState } from 'react';

type InstallEvent = Event & { prompt: () => Promise<void>; userChoice: Promise<{ outcome: string }> };

const DISMISSED = 'threekey-install-dismissed';

function installed() {
  return matchMedia('(display-mode: standalone)').matches || (navigator as { standalone?: boolean }).standalone === true;
}

function dismissed() {
  try {
    return localStorage.getItem(DISMISSED) === '1';
  } catch {
    return false;
  }
}

export default function InstallHint() {
  const [prompt, setPrompt] = useState<InstallEvent | null>(null);
  const [hidden, setHidden] = useState(() => installed() || dismissed());

  useEffect(() => {
    const onPrompt = (e: Event) => {
      e.preventDefault(); // show our own button instead of the browser's mini bar
      setPrompt(e as InstallEvent);
    };
    const onInstalled = () => setHidden(true);
    addEventListener('beforeinstallprompt', onPrompt);
    addEventListener('appinstalled', onInstalled);
    return () => {
      removeEventListener('beforeinstallprompt', onPrompt);
      removeEventListener('appinstalled', onInstalled);
    };
  }, []);

  if (hidden) return null;

  const hide = () => {
    try {
      localStorage.setItem(DISMISSED, '1');
    } catch {
      // private mode: it just shows again next time
    }
    setHidden(true);
  };

  const ios = /iPhone|iPad|iPod/.test(navigator.userAgent);

  return (
    <section className="card install">
      <p className="label">Put Krew on your home screen</p>
      {prompt ? (
        <button
          className="primary"
          onClick={() => prompt.prompt().then(() => prompt.userChoice).then(c => c.outcome === 'accepted' && setHidden(true))}
        >
          Install app
        </button>
      ) : ios ? (
        <ol className="steps">
          <li>Tap the <b>Share</b> button <span aria-hidden="true">(the square with an arrow ⬆)</span> at the bottom of Safari</li>
          <li>Scroll down and tap <b>Add to Home Screen</b>, then <b>Add</b></li>
        </ol>
      ) : (
        <ol className="steps">
          <li>Tap the browser menu <b>⋮</b> (top right in Chrome)</li>
          <li>Tap <b>Add to Home screen</b> or <b>Install app</b></li>
        </ol>
      )}
      <button className="link" onClick={hide}>Not now</button>
    </section>
  );
}
