// ─────────────────────────────────────────────────────────────────────────────
// Config — fill APPS_SCRIPT_URL after deploying scripts/apps_script.gs
// ─────────────────────────────────────────────────────────────────────────────
const APPS_SCRIPT_URL = 'https://script.google.com/macros/s/AKfycbygSsO7AtvvORFXO6ItV7TgWxFOGvl59RtGuq8g-jrjjgyHYUJhjumS2DpDLtIFcqtH/exec';
const FIXTURES_URL    = '../data/fixtures/fixtures.json';
const FREEZE_LEAD_MS  = 60 * 60 * 1000; // lock picks 1 h before earliest kickoff
const LS_NAME_KEY     = 'fa_name';
const LS_PICKS_KEY    = 'fa_picks';

// ─────────────────────────────────────────────────────────────────────────────
// State
// ─────────────────────────────────────────────────────────────────────────────
let fixtures      = [];
let activeDay     = null;
let userName      = '';
let userSlug      = '';
let picks         = {};         // { match_id: { p_home, p_draw, p_away } }
let sliders       = {};         // { match_id: noUiSlider instance }
let cardObserver  = null;

// ─────────────────────────────────────────────────────────────────────────────
// Utilities
// ─────────────────────────────────────────────────────────────────────────────

function toSlug(name) {
  return name.trim().toLowerCase()
    .normalize('NFD').replace(/[̀-ͯ]/g, '')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '');
}

function fmtKickoff(iso) {
  return new Date(iso).toLocaleString('en-GB', {
    weekday: 'short', day: 'numeric', month: 'short',
    hour: '2-digit', minute: '2-digit', timeZone: 'UTC', timeZoneName: 'short',
  });
}

function fmtCountdown(ms) {
  if (ms <= 0) return 'now';
  const h = Math.floor(ms / 3_600_000);
  const m = Math.floor((ms % 3_600_000) / 60_000);
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

function groupByDate(all) {
  // Group by UTC-8 date so late-night US games (e.g. 04:00Z) stay with
  // the same campaign day as the earlier matches they're frozen alongside.
  const OFFSET_MS = 8 * 60 * 60 * 1000;
  const map = {};
  for (const f of all) {
    const day = new Date(new Date(f.kickoff_utc).getTime() - OFFSET_MS)
      .toISOString().slice(0, 10);
    (map[day] ??= []).push(f);
  }
  return Object.entries(map)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, matches]) => ({ date, matches }));
}

function getActiveMatchday(all) {
  const now = Date.now();
  for (const day of groupByDate(all)) {
    const earliest = Math.min(...day.matches.map(m => +new Date(m.kickoff_utc)));
    const freezeAt = earliest - FREEZE_LEAD_MS;
    if (now < freezeAt)  return { ...day, status: 'open',   freezeAt, earliest };
    if (now < earliest)  return { ...day, status: 'locked', freezeAt, earliest };
  }
  return { status: 'over' };
}

// ─────────────────────────────────────────────────────────────────────────────
// Screen switching
// ─────────────────────────────────────────────────────────────────────────────

function showScreen(id) {
  document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
  document.getElementById('screen-' + id).classList.add('active');
}

// ─────────────────────────────────────────────────────────────────────────────
// localStorage
// ─────────────────────────────────────────────────────────────────────────────

function loadState() {
  userName = localStorage.getItem(LS_NAME_KEY) || '';
  userSlug = userName ? toSlug(userName) : '';
  picks    = JSON.parse(localStorage.getItem(LS_PICKS_KEY) || '{}');
}

function savePick(matchId, home, draw, away) {
  picks[matchId] = { p_home: home / 100, p_draw: draw / 100, p_away: away / 100 };
  localStorage.setItem(LS_PICKS_KEY, JSON.stringify(picks));
}

// ─────────────────────────────────────────────────────────────────────────────
// Card builders
// ─────────────────────────────────────────────────────────────────────────────

function buildMatchCard(match, idx) {
  const saved  = picks[match.match_id];
  const h0     = saved ? Math.round(saved.p_home * 100) : 40;
  const d0     = saved ? Math.round(saved.p_draw * 100) : 30;
  const a0     = 100 - h0 - d0;
  const filled = !!saved;

  const card       = document.createElement('article');
  card.className   = 'card match-card';
  card.dataset.idx = idx;
  card.dataset.mid = match.match_id;
  card.setAttribute('role', 'listitem');

  card.innerHTML = `
    <div class="card-inner">
      <div class="card-meta">
        <span class="stage-badge">${match.stage}</span>
        ${filled ? '<span class="filled-badge">✓ filled</span>' : ''}
      </div>

      <div class="teams-row">
        <div class="team">
          <div class="team-name">${match.home || 'TBD'}</div>
        </div>
        <div class="vs-badge">vs</div>
        <div class="team away">
          <div class="team-name">${match.away || 'TBD'}</div>
        </div>
      </div>

      <div class="kick-time">${fmtKickoff(match.kickoff_utc)}</div>
      <div class="kick-venue">${match.venue}</div>

      <div class="slider-section">
        <div class="slider-el" id="sl-${match.match_id}"></div>
        <div class="pct-row">
          <div class="pct-col home-col">
            <span class="pct-val" id="ph-${match.match_id}">${h0}%</span>
            <span class="pct-lbl">${match.home || 'Home'}</span>
          </div>
          <div class="pct-col draw-col">
            <span class="pct-val" id="pd-${match.match_id}">${d0}%</span>
            <span class="pct-lbl">Draw</span>
          </div>
          <div class="pct-col away-col">
            <span class="pct-val" id="pa-${match.match_id}">${a0}%</span>
            <span class="pct-lbl">${match.away || 'Away'}</span>
          </div>
        </div>
      </div>
    </div>
  `;
  return card;
}

function buildSubmitCard(matches, idx) {
  const card       = document.createElement('article');
  card.className   = 'card submit-card';
  card.dataset.idx = idx;
  card.setAttribute('role', 'listitem');

  const n = matches.filter(m => picks[m.match_id]).length;

  card.innerHTML = `
    <div class="card-inner submit-inner">
      <div class="submit-emoji">⚽</div>
      <h2 class="submit-title">Ready to submit</h2>
      <p class="submit-count" id="submit-count">${n} / ${matches.length} matches filled</p>
      <p class="submit-as">Submitting as <strong id="submit-name"></strong></p>
      <button class="btn-primary" id="btn-submit">Submit picks →</button>
      <p class="submit-note">You can re-submit any time before the freeze — only your latest counts.</p>
      ${!APPS_SCRIPT_URL ? '<p class="submit-warn">⚠ Submission not configured yet.</p>' : ''}
    </div>
  `;
  return card;
}

// ─────────────────────────────────────────────────────────────────────────────
// Slider init
// ─────────────────────────────────────────────────────────────────────────────

function initSlider(matchId, frozen) {
  const el = document.getElementById('sl-' + matchId);
  if (!el) return;

  const saved = picks[matchId];
  const h     = saved ? Math.round(saved.p_home * 100) : 40;
  const d     = saved ? Math.round(saved.p_draw * 100) : 30;

  const sl = noUiSlider.create(el, {
    start:     [h, h + d],
    connect:   [true, true, true],
    range:     { min: 1, max: 99 },
    step:      1,
    behaviour: 'drag',
  });

  if (frozen) {
    sl.disable();
    return;
  }

  sl.on('update', ([v1, v2]) => {
    const home = Math.round(v1);
    const away = 100 - Math.round(v2);
    const draw = 100 - home - away;

    document.getElementById('ph-' + matchId).textContent = home + '%';
    document.getElementById('pd-' + matchId).textContent = draw + '%';
    document.getElementById('pa-' + matchId).textContent = away + '%';

    savePick(matchId, home, draw, away);
    refreshSubmitCount();
    refreshFilledBadge(matchId);
  });

  sliders[matchId] = sl;
}

function refreshFilledBadge(matchId) {
  const card  = document.querySelector(`[data-mid="${matchId}"]`);
  const badge = card?.querySelector('.filled-badge');
  if (!badge) return;
  if (picks[matchId]) {
    badge.textContent = '✓ filled';
    badge.hidden = false;
  }
}

function refreshSubmitCount() {
  const el = document.getElementById('submit-count');
  if (!el || !activeDay) return;
  const n = activeDay.matches.filter(m => picks[m.match_id]).length;
  el.textContent = `${n} / ${activeDay.matches.length} matches filled`;
}

// ─────────────────────────────────────────────────────────────────────────────
// Render cards
// ─────────────────────────────────────────────────────────────────────────────

function renderCards() {
  const track = document.getElementById('cards-track');
  track.innerHTML = '';
  sliders = {};

  if (cardObserver) cardObserver.disconnect();

  const { matches, status, earliest, freezeAt } = activeDay;
  const frozen = status === 'locked';
  const total  = matches.length;

  // Matchday banner
  const banner = document.getElementById('matchday-banner');
  if (frozen) {
    banner.textContent = `🔒 Picks locked — first kickoff ${fmtKickoff(new Date(earliest).toISOString())}`;
    banner.hidden = false;
  } else {
    const remaining = fmtCountdown(freezeAt - Date.now());
    banner.textContent = `🟢 Picks open · Freeze in ${remaining}`;
    banner.hidden = false;
  }

  // Build match cards
  matches.forEach((match, idx) => {
    track.appendChild(buildMatchCard(match, idx));
  });

  // Submit card (last in the swipe sequence)
  track.appendChild(buildSubmitCard(matches, total));

  // Hydrate submit card
  const nameEl = document.getElementById('submit-name');
  if (nameEl) nameEl.textContent = userName;

  // Progress observer
  cardObserver = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (e.isIntersecting && e.intersectionRatio >= 0.5) {
        const idx       = parseInt(e.target.dataset.idx);
        const display   = Math.min(idx + 1, total);
        document.getElementById('progress-text').textContent = `${display} / ${total}`;
        const pct = total > 0 ? (idx / total) * 100 : 100;
        document.getElementById('progress-fill').style.width = pct + '%';
      }
    }
  }, { threshold: 0.5 });

  track.querySelectorAll('.card').forEach(c => cardObserver.observe(c));

  // Init sliders on next frame (DOM must be painted)
  requestAnimationFrame(() => {
    matches.forEach(m => initSlider(m.match_id, frozen));
  });

  // Submit button
  document.getElementById('btn-submit')?.addEventListener('click', submitPicks);
}

// ─────────────────────────────────────────────────────────────────────────────
// Submit
// ─────────────────────────────────────────────────────────────────────────────

async function submitPicks() {
  if (!APPS_SCRIPT_URL) {
    alert('Submission not configured yet. Please check back soon!');
    return;
  }

  const payload = activeDay.matches
    .filter(m => picks[m.match_id])
    .map(m => ({
      name:    userName,
      slug:    userSlug,
      match_id: m.match_id,
      p_home:  picks[m.match_id].p_home,
      p_draw:  picks[m.match_id].p_draw,
      p_away:  picks[m.match_id].p_away,
    }));

  if (payload.length === 0) {
    alert('Fill in at least one match before submitting.');
    return;
  }

  const btn = document.getElementById('btn-submit');
  btn.disabled    = true;
  btn.textContent = 'Submitting…';

  try {
    // mode: 'no-cors' avoids CORS preflight which Apps Script doesn't support.
    // Response is opaque so we can't read it, but the POST reaches the sheet.
    await fetch(APPS_SCRIPT_URL, {
      method: 'POST',
      mode:   'no-cors',
      body:   JSON.stringify(payload),
    });
    showConfirmation(payload.length);
  } catch {
    btn.disabled    = false;
    btn.textContent = 'Submit picks →';
    alert('Submission failed — check your connection and try again.');
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Confirmation screen
// ─────────────────────────────────────────────────────────────────────────────

function showConfirmation(n) {
  const detail = document.getElementById('confirm-detail');
  detail.textContent = `${n} match${n !== 1 ? 'es' : ''} submitted · ${userName}`;

  const btnShare = document.getElementById('btn-share');
  btnShare.onclick = () => {
    const text = `I've submitted my World Cup 2026 predictions on The Forecasting Arena — humans vs AI 🤖⚽`;
    if (navigator.share) {
      navigator.share({ text, url: window.location.href }).catch(() => {});
    } else {
      navigator.clipboard.writeText(`${text}\n${window.location.href}`);
      btnShare.textContent = 'Copied!';
    }
  };

  document.getElementById('btn-edit').onclick = () => {
    showScreen('cards');
    // Scroll back to first card
    document.getElementById('cards-track')?.querySelector('.card')
      ?.scrollIntoView({ inline: 'start', block: 'nearest' });
  };

  showScreen('confirm');
}

// ─────────────────────────────────────────────────────────────────────────────
// Keyboard navigation (desktop)
// ─────────────────────────────────────────────────────────────────────────────

function setupKeyboard() {
  document.addEventListener('keydown', (e) => {
    if (!document.getElementById('screen-cards').classList.contains('active')) return;
    const cards  = document.querySelectorAll('#cards-track .card');
    const track  = document.getElementById('cards-track');
    const width  = track.offsetWidth;
    const curr   = Math.round(track.scrollLeft / width);

    if (e.key === 'ArrowRight' && curr < cards.length - 1) {
      track.scrollTo({ left: (curr + 1) * width, behavior: 'smooth' });
    } else if (e.key === 'ArrowLeft' && curr > 0) {
      track.scrollTo({ left: (curr - 1) * width, behavior: 'smooth' });
    }
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// Init
// ─────────────────────────────────────────────────────────────────────────────

async function init() {
  loadState();

  // Load fixtures
  let res;
  try {
    res = await fetch(FIXTURES_URL);
    fixtures = await res.json();
  } catch {
    document.body.innerHTML =
      '<p style="padding:2rem;color:#dc2626">Failed to load fixtures — please refresh.</p>';
    return;
  }

  activeDay = getActiveMatchday(fixtures);

  if (activeDay.status === 'over') {
    document.body.innerHTML = `
      <div style="padding:2rem;text-align:center;font-family:sans-serif">
        <div style="font-size:3rem">🏆</div>
        <h2>Tournament complete!</h2>
        <p>Check the leaderboard for final standings.</p>
      </div>`;
    return;
  }

  // Name screen
  const nameInput = document.getElementById('name-input');
  if (userName) nameInput.value = userName;

  document.getElementById('btn-continue').addEventListener('click', () => {
    const name = nameInput.value.trim();
    if (!name) { nameInput.focus(); return; }
    userName = name;
    userSlug = toSlug(name);
    localStorage.setItem(LS_NAME_KEY, name);
    document.getElementById('user-chip').textContent = name;
    showScreen('cards');
    renderCards();
  });

  nameInput.addEventListener('keydown', e => {
    if (e.key === 'Enter') document.getElementById('btn-continue').click();
  });

  document.getElementById('btn-back').addEventListener('click', () => showScreen('name'));

  setupKeyboard();

  // Skip name screen if name already saved
  if (userName) {
    document.getElementById('user-chip').textContent = userName;
    showScreen('cards');
    renderCards();
  } else {
    showScreen('name');
    nameInput.focus();
  }
}

document.addEventListener('DOMContentLoaded', init);
