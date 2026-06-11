// ─────────────────────────────────────────────────────────────────────────────
// Config — fill APPS_SCRIPT_URL after deploying scripts/apps_script.gs
// ─────────────────────────────────────────────────────────────────────────────
const APPS_SCRIPT_URL   = 'https://script.google.com/macros/s/AKfycbygSsO7AtvvORFXO6ItV7TgWxFOGvl59RtGuq8g-jrjjgyHYUJhjumS2DpDLtIFcqtH/exec';
const FIXTURES_URL      = '../data/fixtures/fixtures.json';
const CONTEXTS_URL      = '../data/reference/match_contexts.json';
const FREEZE_LEAD_MS    = 60 * 60 * 1000;  // safety margin before first kickoff
const FREEZE_HOUR_UTC   = 18;              // freeze.yml cron hour — keep in sync
const LS_NAME_KEY       = 'fa_name';
const LS_PICKS_KEY      = 'fa_picks';

let matchContexts = {};  // { match_id: context_object }

// ─────────────────────────────────────────────────────────────────────────────
// Dark mode toggle
// ─────────────────────────────────────────────────────────────────────────────

function setupThemeToggle() {
  const btn  = document.getElementById('theme-toggle');
  const icon = btn?.querySelector('.theme-icon');
  if (!btn || !icon) return;

  function syncIcon() {
    icon.textContent = document.documentElement.getAttribute('data-theme') === 'dark' ? '☀️' : '🌙';
  }

  syncIcon();

  btn.addEventListener('click', () => {
    const dark = document.documentElement.getAttribute('data-theme') === 'dark';
    document.documentElement.setAttribute('data-theme', dark ? 'light' : 'dark');
    localStorage.setItem('fa_theme', dark ? 'light' : 'dark');
    syncIcon();
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// Expandable feature tiles (landing screen)
// ─────────────────────────────────────────────────────────────────────────────

const FEATURE_INFO = {
  ai: "Five models compete on every match: Gemini 2.5 Flash (Google), Llama 3.3 70B (Meta), Gemma 4 31B (Google), GPT-OSS-120B (OpenAI's open-weights reasoning model), and GPT-4o Mini (OpenAI). Each model receives identical statistical context: Elo ratings, last-10 form, head-to-head history, and venue conditions — the same data you see on each card. No internet browsing, no live news.",
  brier: "We use the multiclass Brier score: the sum of squared errors across all three outcomes (home win, draw, away win). Perfect prediction = 0.0. Maximally wrong (100% on the losing side) = 2.0. A uniform 33%/33%/33% guess = 0.667. It punishes overconfidence — saying 90% on a team that loses hurts far more than saying 50%. You can't win by always going all-in.",
  live: "Rankings update after every full-time result. Every prediction is committed to a public GitHub repository before kickoff — immutable, timestamped, auditable. Anyone can verify that no model or human changed their prediction after seeing the result. The git history is the official record.",
};

function setupFeatureTiles() {
  const detail     = document.getElementById('feature-detail');
  const detailText = document.getElementById('feature-detail-text');
  if (!detail || !detailText) return;

  let activeKey = null;

  document.querySelectorAll('.feature-item[data-key]').forEach(item => {
    item.addEventListener('click', () => {
      const key = item.dataset.key;
      if (activeKey === key) {
        detail.hidden = true;
        item.classList.remove('active-feature');
        activeKey = null;
        return;
      }
      document.querySelectorAll('.feature-item').forEach(i => i.classList.remove('active-feature'));
      item.classList.add('active-feature');
      detailText.textContent = FEATURE_INFO[key] || '';
      detail.hidden = false;
      activeKey = key;
    });
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// Flag emoji helpers
// ─────────────────────────────────────────────────────────────────────────────

const FIFA_TO_ISO = {
  ALG:'DZ', ARG:'AR', AUS:'AU', AUT:'AT', BEL:'BE', BRA:'BR', CAN:'CA',
  CIV:'CI', COL:'CO', CPV:'CV', CRO:'HR', CUR:'CW', ECU:'EC', EGY:'EG',
  ESP:'ES', FRA:'FR', GER:'DE', GHA:'GH', HAI:'HT', IRN:'IR', JOR:'JO',
  JPN:'JP', KOR:'KR', KSA:'SA', MAR:'MA', MEX:'MX', NED:'NL', NOR:'NO',
  NZL:'NZ', PAN:'PA', PAR:'PY', POR:'PT', QAT:'QA', RSA:'ZA', SEN:'SN',
  SUI:'CH', TUN:'TN', URU:'UY', USA:'US', UZB:'UZ',
  BIH:'BA', SWE:'SE', TUR:'TR', CZE:'CZ', COD:'CD', IRQ:'IQ',
};

const SPECIAL_FLAGS = { ENG:'🏴󠁧󠁢󠁥󠁮󠁧󠁿', SCO:'🏴󠁧󠁢󠁳󠁣󠁴󠁿', WAL:'🏴󠁧󠁢󠁷󠁬󠁳󠁿' };

function teamFlag(code) {
  if (!code) return '🌍';
  if (SPECIAL_FLAGS[code]) return SPECIAL_FLAGS[code];
  const iso = FIFA_TO_ISO[code];
  if (!iso) return '🏳️';
  return [...iso].map(c => String.fromCodePoint(c.charCodeAt(0) + 127397)).join('');
}

// ─────────────────────────────────────────────────────────────────────────────
// Fun facts (per team, fallback from home → away)
// ─────────────────────────────────────────────────────────────────────────────

const MATCHUP_FACTS = {
  'ARG-BRA': 'The Superclásico: the fiercest rivalry in South American football. Brazil lead the World Cup h2h 2-1 (1 draw).',
  'BRA-ARG': 'The Superclásico! Brazil vs Argentina at a World Cup is as big as it gets — Brazil lead the WC h2h 2-1.',
  'ENG-GER': 'England beat West Germany 4-2 in the 1966 final. Germany have won every WC knockout tie between them since.',
  'GER-ENG': 'Germany have not lost a World Cup knockout match to England since the 1966 final.',
  'USA-MEX': "USA vs Mexico — CONCACAF's fiercest rivalry. The USMNT won 2-0 so many times it became a chant: 'Dos a cero'.",
  'MEX-USA': 'Mexico vs USA is the most-played international rivalry in CONCACAF history.',
};

const TEAM_FACTS = {
  ARG: 'Defending champions! Argentina beat France on penalties in Qatar 2022 🏆',
  BRA: 'Brazil are the most successful WC nation ever, with 5 titles (1958-2002).',
  FRA: 'France won it in 2018 and were runners-up in 2022 — back-to-back finals.',
  GER: '4-time World Cup winners — level with Italy as the most successful European nation.',
  ENG: "England's only World Cup win was on home soil in 1966. The 60-year wait continues…",
  ESP: "Spain's 2010 win was the first World Cup won by a European team outside Europe.",
  NED: 'The Netherlands have been World Cup runners-up 3 times (1974, 1978, 2010) — never won.',
  URU: 'Uruguay won the very first World Cup in 1930, then again in 1950 (the Maracanazo).',
  MEX: 'Mexico has played in every World Cup since 1994 without missing one. The Azteca has hosted 2 finals.',
  USA: 'The USA last hosted the World Cup in 1994 — they reached the Round of 16 that year.',
  CAN: "Canada went goalless at their 1986 debut; Alphonso Davies scored their first-ever WC goal in 2022. Now they're co-hosts.",
  MAR: 'Morocco made history in 2022 as the first African team ever to reach a World Cup semi-final.',
  KOR: 'South Korea finished 4th in 2002 — still the best World Cup result by any Asian nation.',
  JPN: 'Japan stunned both Germany and Spain in 2022, topping their group before losing on penalties.',
  KSA: 'Saudi Arabia pulled off one of the greatest WC upsets: beating Argentina 2-1 in Qatar 2022.',
  AUS: "Australia's Socceroos reached the Round of 16 in 2022, matching their best ever World Cup run (2006).",
  SEN: 'Senegal won their first AFCON in 2021 and have reached the knockouts in two of their three World Cups.',
  CRO: '3rd in 2022, 2nd in 2018 — Croatia keep overachieving with a population of just 4 million.',
  NOR: 'Norway famously beat Brazil 2-1 at France 98 — still one of the biggest WC upsets.',
  ECU: 'Ecuador opened the Qatar 2022 tournament with a victory against the host nation.',
  GHA: "Ghana came agonisingly close to a WC semi-final in 2010 before Suárez's infamous handball.",
  ALG: "Algeria's 1982 group stage fate inspired FIFA to make all final group games simultaneous.",
  SUI: 'Switzerland are at their 13th World Cup — but have never gone past the quarter-finals.',
  CIV: "Ivory Coast's golden generation — Drogba, the Touré brothers, Gervinho — 3 WCs in a row.",
  QAT: 'Qatar 2022 hosts became the first host nation eliminated in the group stage.',
  BEL: "Belgium's 'golden generation' finished 3rd in 2018 — their best World Cup result.",
  COL: 'Colombia reached the quarter-finals in 2014; James Rodríguez won the Golden Boot.',
  IRN: "Iran's most celebrated WC moment: beating the USA 2-1 at France 1998.",
  NZL: 'New Zealand went the entire 2010 World Cup unbeaten — they drew all 3 group games.',
  PAN: 'Panama only reached their first-ever World Cup in 2018, just 8 years ago.',
  PAR: 'Paraguay reached the quarter-finals in 2010 — their best ever World Cup performance.',
  HAI: "Haiti's only previous World Cup was 1974 — over 50 years ago.",
  EGY: 'Egypt are the most successful team in African Cup of Nations history with 7 titles.',
  TUN: 'Tunisia are at their 7th World Cup — they have never yet reached the knockout rounds.',
  POR: 'Portugal\'s Eusébio won the Golden Boot at the 1966 World Cup — on the same stage that England won.',
  CUR: 'Curaçao (pop. ~150,000) is one of the smallest nations ever to qualify for a World Cup 🏝️',
  CPV: 'Cape Verde — a volcanic archipelago off West Africa — are making their World Cup debut!',
  UZB: 'Uzbekistan are one of the more surprising qualifiers, making their World Cup debut.',
  JOR: 'Jordan are making their historic first-ever World Cup appearance 🎉',
  CZE: 'Czechia knocked out Denmark on penalties in the playoffs — their first World Cup as an independent nation since 2006.',
  TUR: 'Türkiye finished 3rd at the 2002 World Cup — their first appearance since then came via a playoff win over Kosovo.',
  SWE: 'Sweden beat Poland 3-2 in the playoff final — their first World Cup since 2018.',
  BIH: 'Bosnia and Herzegovina beat ITALY on penalties to qualify — Italy miss a third straight World Cup.',
  COD: 'DR Congo qualified for the first time since 1974, when they played as Zaire.',
  IRQ: "Iraq's first World Cup since 1986 — they beat Bolivia in the playoff final to get here.",
};

function getMatchFact(match) {
  const k  = `${match.home_code}-${match.away_code}`;
  const rk = `${match.away_code}-${match.home_code}`;
  return MATCHUP_FACTS[k] || MATCHUP_FACTS[rk]
      || TEAM_FACTS[match.home_code] || TEAM_FACTS[match.away_code] || null;
}

// ─────────────────────────────────────────────────────────────────────────────
// Match Intel panel (context from match_contexts.json)
// ─────────────────────────────────────────────────────────────────────────────

function formDot(result) {
  const cls = result === 'W' ? 'dot-w' : result === 'D' ? 'dot-d' : 'dot-l';
  return `<span class="form-dot ${cls}" title="${result}"></span>`;
}

function buildIntelPanel(match) {
  const ctx = matchContexts[match.match_id];
  if (!ctx || ctx.placeholder) return '';

  const homeElo = ctx.home_elo || 0;
  const awayElo = ctx.away_elo || 0;
  const eloDiff = homeElo - awayElo;
  const homeForm5 = (ctx.home_form5 || []).map(formDot).join('');
  const awayForm5 = (ctx.away_form5 || []).map(formDot).join('');

  const h2hStr = ctx.h2h_played > 0
    ? `H2H: ${ctx.h2h_home_wins}W ${ctx.h2h_draws}D ${ctx.h2h_away_wins}L (${ctx.h2h_played} played)`
    : 'H2H: No previous meetings';

  const altitudeHtml = ctx.altitude_m > 800
    ? `<div class="intel-altitude">⛰️ ${ctx.altitude_m.toLocaleString()}m altitude</div>`
    : (ctx.climate_note && ctx.altitude_m === 0 || ctx.altitude_m < 800)
      ? `<div class="intel-climate">🌡️ ${ctx.climate_note || ''}</div>`
      : '';

  return `
    <div class="intel-panel">
      <div class="intel-header">📊 Match Intel</div>
      <div class="intel-grid">
        <div class="intel-col">
          <div class="intel-elo home-elo">${homeElo}</div>
          <div class="intel-form-row">${homeForm5}</div>
        </div>
        <div class="intel-mid">
          <div class="intel-elo-label">Elo</div>
          <div class="intel-elo-diff ${eloDiff > 50 ? 'home-fav' : eloDiff < -50 ? 'away-fav' : 'even'}">
            ${Math.abs(eloDiff) > 30 ? (eloDiff > 0 ? '▲' : '▼') + Math.abs(eloDiff) : '≈'}
          </div>
          <div class="intel-form-label">Last 5</div>
        </div>
        <div class="intel-col intel-col-right">
          <div class="intel-elo away-elo">${awayElo}</div>
          <div class="intel-form-row">${awayForm5}</div>
        </div>
      </div>
      <div class="intel-h2h">${h2hStr}</div>
      ${altitudeHtml}
      <div class="intel-note">Elo rates team strength from past results — higher is stronger, and a gap of 100+ points is a clear edge.</div>
    </div>`;
}

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
    hour: '2-digit', minute: '2-digit', timeZoneName: 'short',
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

// The REAL freeze is the freeze.yml cron (18:00 UTC on the campaign day), or
// earlier if a kickoff is unusually early. The banner must never promise a
// later freeze than the one that actually runs — a pick after the real freeze
// is silently void, which is worse than showing "locked" early.
function freezeTimeFor(dateStr, earliestKickoff) {
  const [y, mo, d] = dateStr.split('-').map(Number);
  const cronMs = Date.UTC(y, mo - 1, d, FREEZE_HOUR_UTC);
  return Math.min(cronMs, earliestKickoff - FREEZE_LEAD_MS);
}

function getActiveMatchday(all) {
  const now = Date.now();
  for (const day of groupByDate(all)) {
    const earliest = Math.min(...day.matches.map(m => +new Date(m.kickoff_utc)));
    const freezeAt = freezeTimeFor(day.date, earliest);
    if (now < freezeAt)  return { ...day, status: 'open',   freezeAt, earliest };
    if (now < earliest)  return { ...day, status: 'locked', freezeAt, earliest };
  }
  return { status: 'over' };
}

// ─────────────────────────────────────────────────────────────────────────────
// Screen switching (CSS opacity transition handles the animation)
// ─────────────────────────────────────────────────────────────────────────────

function showScreen(id) {
  document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
  document.getElementById('screen-' + id).classList.add('active');
}

// ─────────────────────────────────────────────────────────────────────────────
// Desktop navigation arrows
// ─────────────────────────────────────────────────────────────────────────────

function setupNavArrows() {
  const track = document.getElementById('cards-track');
  const prev  = document.getElementById('nav-prev');
  const next  = document.getElementById('nav-next');
  if (!track || !prev || !next) return;

  function scrollTo(dir) {
    const w    = track.clientWidth;
    const curr = Math.round(track.scrollLeft / w);
    const dest = dir === 'next' ? curr + 1 : curr - 1;
    const max  = track.querySelectorAll('.card').length - 1;
    if (dest < 0 || dest > max) return;
    track.scrollTo({ left: dest * w, behavior: 'smooth' });
  }

  prev.addEventListener('click', () => scrollTo('prev'));
  next.addEventListener('click', () => scrollTo('next'));

  function updateArrows() {
    const w    = track.clientWidth;
    const curr = Math.round(track.scrollLeft / w);
    const max  = track.querySelectorAll('.card').length - 1;
    prev.style.opacity = curr === 0   ? '0.3' : '1';
    next.style.opacity = curr === max ? '0.3' : '1';
    prev.style.pointerEvents = curr === 0   ? 'none' : 'auto';
    next.style.pointerEvents = curr === max ? 'none' : 'auto';
  }

  track.addEventListener('scroll', updateArrows, { passive: true });
  updateArrows();
}

// ─────────────────────────────────────────────────────────────────────────────
// 3D — scroll-linked card carousel + pointer tilt
// Both are skipped entirely for users with prefers-reduced-motion.
// ─────────────────────────────────────────────────────────────────────────────

const REDUCED_MOTION = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

function setup3DCarousel() {
  if (REDUCED_MOTION) return;
  const track = document.getElementById('cards-track');
  if (!track) return;

  let raf = null;
  function apply() {
    raf = null;
    const w = track.clientWidth;
    if (!w) return;
    track.querySelectorAll('.card').forEach(card => {
      const inner = card.querySelector('.card-inner');
      if (!inner) return;
      // t = 0 when centred, ±1 when one full viewport away
      const t = Math.max(-1, Math.min(1, (card.offsetLeft - track.scrollLeft) / w));
      const abs = Math.abs(t);
      inner.style.transform =
        `perspective(1200px) rotateY(${(-t * 16).toFixed(2)}deg)` +
        ` translateZ(${(-abs * 90).toFixed(1)}px)` +
        ` scale(${(1 - abs * 0.06).toFixed(3)})`;
      inner.style.opacity = String(1 - abs * 0.3);
    });
  }
  track.addEventListener('scroll', () => {
    if (!raf) raf = requestAnimationFrame(apply);
  }, { passive: true });
  window.addEventListener('resize', () => {
    if (!raf) raf = requestAnimationFrame(apply);
  }, { passive: true });
  apply();
}

function setupTilt() {
  if (REDUCED_MOTION) return;
  if (!window.matchMedia('(hover: hover)').matches) return;
  document.querySelectorAll('[data-tilt]').forEach(el => {
    el.addEventListener('mousemove', e => {
      const r = el.getBoundingClientRect();
      const x = (e.clientX - r.left) / r.width  - 0.5;
      const y = (e.clientY - r.top)  / r.height - 0.5;
      el.style.transform =
        `perspective(700px) rotateY(${(x * 10).toFixed(2)}deg)` +
        ` rotateX(${(-y * 8).toFixed(2)}deg) translateZ(8px)`;
    });
    el.addEventListener('mouseleave', () => { el.style.transform = ''; });
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// Confetti burst (confirmation screen)
// ─────────────────────────────────────────────────────────────────────────────

function spawnConfetti() {
  const container = document.getElementById('screen-confirm');
  if (!container) return;
  const colors = ['#7c3aed','#0891b2','#f0c040','#22c55e','#f43f5e','#fb923c'];
  const shapes = ['2px','4px','6px'];

  for (let i = 0; i < 72; i++) {
    const el = document.createElement('div');
    el.className = 'confetti-piece';
    const size = shapes[Math.floor(Math.random() * shapes.length)];
    el.style.cssText = [
      `left:${Math.random() * 100}%`,
      `background:${colors[Math.floor(Math.random() * colors.length)]}`,
      `width:${6 + Math.random() * 6}px`,
      `height:${8 + Math.random() * 8}px`,
      `border-radius:${Math.random() > .5 ? '50%' : '2px'}`,
      `animation-duration:${1.2 + Math.random() * 1.4}s`,
      `animation-delay:${Math.random() * 0.6}s`,
      `transform:rotate(${Math.random() * 360}deg)`,
    ].join(';');
    container.appendChild(el);
    setTimeout(() => el.remove(), 2800);
  }
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
  const saved       = picks[match.match_id];
  const h0          = saved ? Math.round(saved.p_home * 100) : 40;
  const d0          = saved ? Math.round(saved.p_draw * 100) : 30;
  const a0          = 100 - h0 - d0;
  const filled      = !!saved;
  const placeholder = !!match.is_placeholder;
  const homeFlag    = teamFlag(match.home_code);
  const awayFlag    = placeholder ? '⏳' : teamFlag(match.away_code);
  const fact        = getMatchFact(match);

  const sliderHtml = placeholder
    ? `<div class="tbd-panel">
         <div class="tbd-title">Opponent not confirmed yet</div>
         <div class="tbd-sub">This match opens for picks once the team is decided.
         It doesn't count towards your total.</div>
       </div>`
    : `<div class="slider-section">
        <div class="slider-el" id="sl-${match.match_id}"></div>
        <div class="pct-row">
          <div class="pct-col home-col">
            <span class="pct-val" id="ph-${match.match_id}">${h0}%</span>
            <span class="pct-lbl" title="${match.home || 'Home'}">${match.home || 'Home'}</span>
          </div>
          <div class="pct-col draw-col">
            <span class="pct-val" id="pd-${match.match_id}">${d0}%</span>
            <span class="pct-lbl">Draw</span>
          </div>
          <div class="pct-col away-col">
            <span class="pct-val" id="pa-${match.match_id}">${a0}%</span>
            <span class="pct-lbl" title="${match.away || 'Away'}">${match.away || 'Away'}</span>
          </div>
        </div>
      </div>`;

  const card       = document.createElement('article');
  card.className   = 'card match-card';
  card.dataset.idx = idx;
  card.dataset.mid = match.match_id;
  card.setAttribute('role', 'listitem');

  card.innerHTML = `
    <div class="card-inner">
      <div class="card-meta">
        <span class="stage-badge">${match.stage}</span>
        <span class="match-num">Match ${match.match_number}</span>
        ${filled ? '<span class="filled-badge">✓</span>' : ''}
      </div>

      <div class="teams-row">
        <div class="team">
          <div class="team-flag">${homeFlag}</div>
          <div class="team-name">${match.home || 'TBD'}</div>
        </div>
        <div class="vs-badge">VS</div>
        <div class="team away">
          <div class="team-flag">${awayFlag}</div>
          <div class="team-name">${match.away || 'TBD'}</div>
        </div>
      </div>

      <div class="match-info-row">
        <span class="kick-time">🕐 ${fmtKickoff(match.kickoff_utc)}</span>
        <span class="kick-venue">📍 ${match.venue}</span>
      </div>

      ${sliderHtml}

      ${placeholder ? '' : buildIntelPanel(match)}

      ${fact ? `<div class="fun-fact">💡 ${fact}</div>` : ''}
    </div>
  `;
  return card;
}

function buildSubmitCard(matches, idx) {
  const card       = document.createElement('article');
  card.className   = 'card submit-card';
  card.dataset.idx = idx;
  card.setAttribute('role', 'listitem');

  const pickable = matches.filter(m => !m.is_placeholder);
  const n = pickable.filter(m => picks[m.match_id]).length;

  card.innerHTML = `
    <div class="card-inner submit-inner">
      <div class="submit-emoji">⚽</div>
      <h2 class="submit-title">Ready to submit</h2>
      <p class="submit-count" id="submit-count">${n} / ${pickable.length} matches filled</p>
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

  sl.on('start', () => navigator.vibrate?.(3));

  function readValues([v1, v2]) {
    const home = Math.round(v1);
    const away = 100 - Math.round(v2);
    return { home, away, draw: 100 - home - away };
  }

  // 'update' fires on bind and on programmatic set() — DISPLAY ONLY.
  // Persisting here would record the 40/30/30 default as a real pick for
  // every card merely rendered (shipped bug, 2026-06-11 — TESTING.md §6 #2).
  sl.on('update', (values) => {
    const { home, draw, away } = readValues(values);
    function setVal(id, val) {
      const el = document.getElementById(id);
      if (!el) return;
      if (el.textContent !== val + '%') {
        el.textContent = val + '%';
        el.classList.remove('pop');
        void el.offsetWidth;
        el.classList.add('pop');
      }
    }
    setVal('ph-' + matchId, home);
    setVal('pd-' + matchId, draw);
    setVal('pa-' + matchId, away);
  });

  // 'slide' only ever fires from user interaction (drag, tap, arrow keys) —
  // this is the only place a pick is recorded.
  sl.on('slide', (values) => {
    const { home, draw, away } = readValues(values);
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
  const pickable = activeDay.matches.filter(m => !m.is_placeholder);
  const n = pickable.filter(m => picks[m.match_id]).length;
  el.textContent = `${n} / ${pickable.length} matches filled`;
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

  // Init sliders on next frame (DOM must be painted); placeholders get none
  requestAnimationFrame(() => {
    matches.filter(m => !m.is_placeholder)
           .forEach(m => initSlider(m.match_id, frozen));
  });

  // Submit button
  document.getElementById('btn-submit')?.addEventListener('click', submitPicks);

  // Desktop navigation arrows
  setupNavArrows();

  // 3D carousel transforms
  setup3DCarousel();

  // Mobile swipe affordance
  setupSwipeHint();
}

// ─────────────────────────────────────────────────────────────────────────────
// Swipe hint (touch devices) — shown until the user swipes once, ever
// ─────────────────────────────────────────────────────────────────────────────

function setupSwipeHint() {
  if (!window.matchMedia('(pointer: coarse)').matches) return;
  if (localStorage.getItem('fa_swipe_hint_done')) return;

  const track   = document.getElementById('cards-track');
  const wrapper = track?.parentElement;
  if (!track || !wrapper || track.querySelectorAll('.card').length < 2) return;

  const hint = document.createElement('div');
  hint.className = 'swipe-hint';
  hint.innerHTML = 'Swipe to see the next match <span class="swipe-hint-arrow">→</span>';
  wrapper.appendChild(hint);

  track.addEventListener('scroll', function dismiss() {
    localStorage.setItem('fa_swipe_hint_done', '1');
    hint.classList.add('swipe-hint-out');
    setTimeout(() => hint.remove(), 400);
    track.removeEventListener('scroll', dismiss);
  }, { passive: true });
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
  requestAnimationFrame(spawnConfetti);
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

  // Load fixtures + context data (context is optional — fails silently)
  let res;
  try {
    res = await fetch(FIXTURES_URL);
    fixtures = await res.json();
  } catch {
    // fetch() can't read local files — a file:// open always lands here.
    const hint = location.protocol === 'file:'
      ? 'This page was opened directly from disk — browsers block data loading ' +
        'over file://. Serve it instead: run <code>python3 -m http.server</code> ' +
        'in the project root and open <code>http://localhost:8000/site/picks.html</code>.'
      : 'Failed to load fixtures — please refresh.';
    document.body.innerHTML =
      `<p style="padding:2rem;color:#dc2626;line-height:1.6;font-family:sans-serif">${hint}</p>`;
    return;
  }

  try {
    const ctxRes = await fetch(CONTEXTS_URL);
    if (ctxRes.ok) matchContexts = await ctxRes.json();
  } catch { /* context unavailable — intel panels just won't show */ }

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
  setupThemeToggle();
  setupFeatureTiles();
  setupTilt();

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
