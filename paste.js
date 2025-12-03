
    /************************************************************************
     * Configuration: Supabase client
     ************************************************************************/
    const SUPABASE_URL = 'https://bkzosvxbkhzkskaejqcb.supabase.co';
    const SUPABASE_ANON_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImJrem9zdnhia2h6a3NrYWVqcWNiIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjQzOTczMzIsImV4cCI6MjA3OTk3MzMzMn0.iqZZCfEtSdWksHGfbxUAOoaInu6ZpR-7mEIRtmvW9io';
    const supabase = supabaseJs.createClient(SUPABASE_URL, SUPABASE_ANON_KEY);

    /************************************************************************
     * Existing constants and helpers (kept from original file)
     ************************************************************************/
    const numRx = /^-?\d+(\.\d+)?$/;
    const attRx = /^[1-5]$/;

    let currentComp = null;      // will hold competition id (integer)
    let currentRound = null;     // will hold round id (integer)
    let currentRoundMeta = null; // round metadata object
    let initLiveDone = false;
    let isLoggedIn = false;

    function escapeHtml(s) {
      if (s == null) return '';
      return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    }

    /* ---------- Time parsing / formatting helpers ---------- */

    // Database stores centiseconds (e.g., 1.09s -> 109). Use null for missing, -1 DNF, -2 DNS.
    function csToDisplay(cs) {
      if (cs === null || cs === undefined || cs === 0) return '';
      if (cs === -1) return 'DNF';
      if (cs === -2) return 'DNS';
      if (!Number.isFinite(Number(cs))) return String(cs);
      const centis = Number(cs);
      const sec = centis / 100.0;
      if (sec >= 60) {
        const mins = Math.floor(sec / 60);
        const secs = sec - mins * 60;
        const secsWhole = Math.floor(secs);
        const secsFrac = Math.round((secs - secsWhole) * 100);
        return `${mins}:${String(secsWhole).padStart(2,'0')}.${String(secsFrac).padStart(2,'0')}`;
      } else {
        return sec.toFixed(2);
      }
    }

    // Accepts strings like "13.09", "1:29.67", numeric seconds, or centiseconds integer string.
    // Returns integer centiseconds or null.
    function parseToCentiseconds(s) {
      if (s === null || s === undefined || s === '') return null;
      if (typeof s === 'number') {
        if (!Number.isFinite(s)) return null;
        // assume seconds if decimal or integer; convert to centiseconds
        return Math.round(s * 100);
      }
      s = String(s).trim();
      // already integer centiseconds
      if (/^-?\d+$/.test(s)) return parseInt(s, 10);
      // mm:ss.xx
      if (s.includes(':')) {
        const parts = s.split(':');
        if (parts.length !== 2) return null;
        const mins = Number(parts[0]);
        const secs = parseFloat(parts[1].replace(',', '.'));
        if (isNaN(mins) || isNaN(secs)) return null;
        return Math.round((mins * 60 + secs) * 100);
      }
      // seconds with decimal
      const sec = parseFloat(s.replace(',', '.'));
      if (!isNaN(sec)) return Math.round(sec * 100);
      return null;
    }

    /* ---------- Ao5 / Mo3 / Bo computations (centiseconds) ---------- */

    function computeAo5(attempts) {
      // attempts: array length up to 5 of centiseconds or null or -1/-2
      const vals = attempts.map(v => (v === null || v === undefined ? null : Number(v)));
      const nonNull = vals.filter(v => v !== null && Number.isFinite(v));
      if (nonNull.length === 0) return { best: null, average: null };
      const best = Math.min(...nonNull);
      if (nonNull.length < 5) return { best, average: null };
      // drop best and worst among the five (treat -1/-2 as large)
      const normalized = vals.map(v => {
        if (v === null) return null;
        if (v === -1 || v === -2) return Number.POSITIVE_INFINITY;
        return v;
      });
      const sorted = normalized.slice().sort((a,b)=>a-b);
      // if any Infinity present among sorted middle, average becomes null
      const middle = sorted.slice(1,4);
      if (middle.some(x => !Number.isFinite(x))) return { best, average: null };
      const avg = Math.round((middle[0] + middle[1] + middle[2]) / 3);
      return { best, average: avg };
    }

    function computeMo3(attempts) {
      const vals = attempts.map(v => (v === null || v === undefined ? null : Number(v)));
      const nonNull = vals.filter(v => v !== null && Number.isFinite(v));
      if (nonNull.length === 0) return { best: null, average: null };
      const best = Math.min(...nonNull);
      if (nonNull.length < 3) return { best, average: null };
      // if any DNF (-1) treat average as null; if DNS (-2) treat as null
      if (nonNull.some(v => v === -1 || v === -2)) return { best, average: null };
      const avg = Math.round((nonNull[0] + nonNull[1] + nonNull[2]) / 3);
      return { best, average: avg };
    }

    function computeBoX(attempts) {
      // For bo3/bo2/bo1 ranking uses best single attempt (min), average not used
      const vals = attempts.map(v => (v === null || v === undefined ? null : Number(v)));
      const nonNull = vals.filter(v => v !== null && Number.isFinite(v));
      if (nonNull.length === 0) return { best: null, average: null };
      const best = Math.min(...nonNull);
      return { best, average: null };
    }

    function computeForFormat(format, attempts) {
      if (!format) return computeAo5(attempts);
      const f = String(format).toLowerCase();
      if (f.startsWith('ao')) return computeAo5(attempts);
      if (f.startsWith('mo')) return computeMo3(attempts);
      if (f.startsWith('bo')) return computeBoX(attempts);
      // fallback
      return computeAo5(attempts);
    }

    /* ---------- Utility: ranking comparator ---------- */

    // value is centiseconds or null. Lower is better. Special values:
    // -2 DNS, -1 DNF -> treated as very large (worse)
    // null or 0 -> treated as missing (worse)
    function rankValueForSort(v) {
      if (v === null || v === undefined || v === 0) return Number.POSITIVE_INFINITY;
      if (v === -2) return Number.POSITIVE_INFINITY;
      if (v === -1) return Number.POSITIVE_INFINITY;
      return Number(v);
    }

    /************************************************************************
     * New functions: fetchMeta, getEventsForComp, getRoundData, initLiveOnce,
     * loadLeaderboard, renderPodiumsView, renderCompetitorsView,
     * renderCompetitorResultsView, loadEditableSheet, submitEdits
     *
     * These replace the previous Google Sheets fetching logic and use Supabase.
     ************************************************************************/

    // fetchMeta: load competitions and rounds metadata
    async function fetchMeta() {
      // returns { competitions: [...], roundsByComp: { compId: [rounds...] } }
      const { data: comps, error: compErr } = await supabase
        .from('competitions')
        .select('*')
        .order('date', { ascending: true });

      if (compErr) {
        console.error('Error fetching competitions', compErr);
        return { competitions: [], roundsByComp: {} };
      }

      const compIds = comps.map(c => c.id);
      const { data: rounds, error: roundsErr } = await supabase
        .from('rounds')
        .select('*')
        .in('comp_id', compIds)
        .order('id', { ascending: true });

      if (roundsErr) {
        console.error('Error fetching rounds', roundsErr);
      }

      const roundsByComp = {};
      (rounds || []).forEach(r => {
        if (!roundsByComp[r.comp_id]) roundsByComp[r.comp_id] = [];
        roundsByComp[r.comp_id].push(r);
      });

      return { competitions: comps, roundsByComp };
    }

    // getEventsForComp: returns rounds for a competition id
    async function getEventsForComp(compId) {
      const { data: rounds, error } = await supabase
        .from('rounds')
        .select('*')
        .eq('comp_id', compId)
        .order('id', { ascending: true });

      if (error) {
        console.error('Error getEventsForComp', error);
        return [];
      }
      return rounds;
    }

    // getRoundData: fetch round_results rows for a given round id
    async function getRoundData(roundId) {
      const { data, error } = await supabase
        .from('round_results')
        .select('*')
        .eq('round_id', roundId)
        .order('id', { ascending: true });

      if (error) {
        console.error('Error fetching round results', error);
        return [];
      }
      return data;
    }

    // initLiveOnce: populate competitions list and wire UI
    async function initLiveOnce() {
      if (initLiveDone) return;
      initLiveDone = true;

      // fetch metadata
      const meta = await fetchMeta();
      const comps = meta.competitions || [];
      const roundsByComp = meta.roundsByComp || {};

      const compListEl = document.getElementById('competitions-list');
      compListEl.innerHTML = '';

      comps.forEach(comp => {
        const btn = document.createElement('button');
        btn.className = 'btn';
        btn.style.display = 'block';
        btn.style.width = '100%';
        btn.style.marginBottom = '6px';
        btn.textContent = `${comp.name} ${comp.date ? '(' + comp.date.split('T')[0] + ')' : ''}`;
        btn.onclick = async () => {
          // set current competition and show rounds
          currentComp = comp.id;
          document.getElementById('live-comp-title').textContent = comp.name;
          document.getElementById('sidebar-comp-name').textContent = comp.name;
          // render rounds cards
          const rounds = roundsByComp[comp.id] || [];
          const wrap = document.getElementById('live-round-cards');
          wrap.innerHTML = '';
          rounds.forEach(r => {
            const card = document.createElement('div');
            card.style.border = '1px solid #eee';
            card.style.padding = '8px';
            card.style.borderRadius = '8px';
            card.style.cursor = 'pointer';
            card.style.minWidth = '120px';
            card.innerHTML = `<div style="font-weight:600">${escapeHtml(String(r.event_id || ''))} ${escapeHtml(String(r.round_code || ''))}</div><div style="font-size:12px;color:#666">${escapeHtml(String(r.format || ''))}</div>`;
            card.onclick = async () => {
              currentRound = r.id;
              currentRoundMeta = r;
              await loadLeaderboard(r.id, r.format);
              // show editor panel if logged in
              document.getElementById('editor-panel').style.display = isLoggedIn ? 'block' : 'none';
              document.getElementById('editing-sheet-name').textContent = `${r.event_id} ${r.round_code}`;
            };
            wrap.appendChild(card);
          });

          // show live competition summary
          document.getElementById('live-competition-summary').style.display = 'block';
          // auto-select first round if exists
          if (rounds.length > 0) {
            const r = rounds[0];
            currentRound = r.id;
            currentRoundMeta = r;
            await loadLeaderboard(r.id, r.format);
            document.getElementById('editing-sheet-name').textContent = `${r.event_id} ${r.round_code}`;
          }
        };
        compListEl.appendChild(btn);
      });

      // wire sidebar buttons
      document.getElementById('btn-sidebar-competitors').onclick = () => {
        renderCompetitorsView();
        showView('view-competitors');
      };
      document.getElementById('btn-sidebar-podiums').onclick = () => {
        renderPodiumsView();
        showView('view-podiums');
      };

      // show live view
      showView('view-live');
    }

    // loadLeaderboard: fetch round data, compute best/average and ranking, render table
    async function loadLeaderboard(roundId, format) {
      // fetch round rows
      const rows = await getRoundData(roundId);
      // compute per-row stats
      const computed = rows.map(row => {
        const attempts = [
          row.attempt1 === null ? null : Number(row.attempt1),
          row.attempt2 === null ? null : Number(row.attempt2),
          row.attempt3 === null ? null : Number(row.attempt3),
          row.attempt4 === null ? null : Number(row.attempt4),
          row.attempt5 === null ? null : Number(row.attempt5)
        ];
        const stats = computeForFormat(format, attempts);
        return {
          id: row.id,
          competitor_name: row.competitor_name,
          competitor_id: row.competitor_id,
          attempts,
          best: stats.best,
          average: stats.average
        };
      });

      // determine ranking key depending on format
      const f = (format || '').toLowerCase();
      let rankingKey = 'best';
      if (f.startsWith('ao') || f.startsWith('mo')) rankingKey = 'average';
      else rankingKey = 'best';

      // sort computed rows
      computed.sort((a,b) => {
        const va = rankValueForSort(a[rankingKey]);
        const vb = rankValueForSort(b[rankingKey]);
        if (va !== vb) return va - vb;
        // tie-breaker: best
        const ba = rankValueForSort(a.best);
        const bb = rankValueForSort(b.best);
        if (ba !== bb) return ba - bb;
        // fallback alphabetical
        return a.competitor_name.localeCompare(b.competitor_name);
      });

      // assign ranks
      let lastVal = null;
      let lastRank = 0;
      computed.forEach((r, idx) => {
        const val = rankValueForSort(r[rankingKey]);
        if (val === lastVal) {
          r.rank = lastRank;
        } else {
          r.rank = idx + 1;
          lastRank = r.rank;
          lastVal = val;
        }
      });

      // render table
      const thead = document.querySelector('#leaderboard thead');
      const tbody = document.querySelector('#leaderboard tbody');
      thead.innerHTML = '';
      tbody.innerHTML = '';

      // header
      const headerRow = document.createElement('tr');
      ['Rank', 'Name', 'Attempts', (rankingKey === 'average' ? 'Average' : 'Best')].forEach(h => {
        const th = document.createElement('th');
        th.textContent = h;
        headerRow.appendChild(th);
      });
      thead.appendChild(headerRow);

      // rows
      computed.forEach(r => {
        const tr = document.createElement('tr');
        const tdRank = document.createElement('td'); tdRank.textContent = r.rank; tr.appendChild(tdRank);
        const tdName = document.createElement('td'); tdName.textContent = r.competitor_name; tr.appendChild(tdName);
        const tdAttempts = document.createElement('td'); tdAttempts.className = 'solves-cell';
        // render attempts
        r.attempts.forEach((a, i) => {
          const span = document.createElement('span');
          span.className = 'solve';
          span.style.marginRight = '6px';
          span.textContent = (a === null || a === 0) ? '' : csToDisplay(a);
          tdAttempts.appendChild(span);
        });
        tr.appendChild(tdAttempts);
        const tdFinal = document.createElement('td');
        const finalVal = r[rankingKey];
        tdFinal.textContent = finalVal === null ? '' : csToDisplay(finalVal);
        tr.appendChild(tdFinal);
        tbody.appendChild(tr);
      });

      // show subtitle
      const subtitle = document.getElementById('subtitle');
      subtitle.textContent = `Round ${currentRoundMeta ? (currentRoundMeta.event_id + ' ' + currentRoundMeta.round_code) : ''} — format ${format || ''}`;
    }

    // renderPodiumsView: compute top 3 per event across rounds of current competition
    async function renderPodiumsView() {
      if (!currentComp) {
        document.getElementById('podiums-results').innerHTML = '<div>No competition selected</div>';
        return;
      }
      // fetch rounds for comp
      const rounds = await getEventsForComp(currentComp);
      const resultsByEvent = {};
      for (const r of rounds) {
        const rows = await getRoundData(r.id);
        // compute stats per row
        const computed = rows.map(row => {
          const attempts = [
            row.attempt1 === null ? null : Number(row.attempt1),
            row.attempt2 === null ? null : Number(row.attempt2),
            row.attempt3 === null ? null : Number(row.attempt3),
            row.attempt4 === null ? null : Number(row.attempt4),
            row.attempt5 === null ? null : Number(row.attempt5)
          ];
          const stats = computeForFormat(r.format, attempts);
          return {
            competitor_name: row.competitor_name,
            best: stats.best,
            average: stats.average
          };
        });

        // ranking key
        const f = (r.format || '').toLowerCase();
        const rankingKey = (f.startsWith('ao') || f.startsWith('mo')) ? 'average' : 'best';
        computed.sort((a,b) => {
          const va = rankValueForSort(a[rankingKey]);
          const vb = rankValueForSort(b[rankingKey]);
          if (va !== vb) return va - vb;
          return a.competitor_name.localeCompare(b.competitor_name);
        });

        // top 3
        resultsByEvent[r.id] = {
          round: r,
          podium: computed.slice(0,3)
        };
      }

      // render
      const wrap = document.getElementById('podiums-results');
      wrap.innerHTML = '';
      for (const key of Object.keys(resultsByEvent)) {
        const info = resultsByEvent[key];
        const card = document.createElement('div');
        card.style.border = '1px solid #eee';
        card.style.padding = '10px';
        card.style.marginBottom = '8px';
        card.style.borderRadius = '8px';
        const title = document.createElement('div');
        title.style.fontWeight = '600';
        title.textContent = `${info.round.event_id} ${info.round.round_code} (${info.round.format})`;
        card.appendChild(title);
        const ol = document.createElement('ol');
        info.podium.forEach(p => {
          const li = document.createElement('li');
          li.textContent = `${p.competitor_name} — ${p.best !== null ? csToDisplay(p.best) : ''}${p.average !== null ? ' / ' + csToDisplay(p.average) : ''}`;
          ol.appendChild(li);
        });
        card.appendChild(ol);
        wrap.appendChild(card);
      }
    }

    // renderCompetitorsView: list unique competitors across all rounds in current competition
    async function renderCompetitorsView() {
      if (!currentComp) {
        document.getElementById('competitors-list').innerHTML = '<div>No competition selected</div>';
        return;
      }
      const rounds = await getEventsForComp(currentComp);
      const competitorSet = new Map(); // name -> {name, id}
      for (const r of rounds) {
        const rows = await getRoundData(r.id);
        rows.forEach(row => {
          if (!competitorSet.has(row.competitor_name)) {
            competitorSet.set(row.competitor_name, { name: row.competitor_name, competitor_id: row.competitor_id });
          }
        });
      }
      const listWrap = document.getElementById('competitors-list');
      listWrap.innerHTML = '';
      const arr = Array.from(competitorSet.values()).sort((a,b)=>a.name.localeCompare(b.name));
      arr.forEach(c => {
        const div = document.createElement('div');
        div.style.padding = '8px';
        div.style.borderBottom = '1px solid #eee';
        div.style.cursor = 'pointer';
        div.textContent = c.name;
        div.onclick = () => {
          renderCompetitorResultsView(c.name);
          showView('view-competitor-results');
        };
        listWrap.appendChild(div);
      });
    }

    // renderCompetitorResultsView: show all results for a competitor across rounds in current competition
    async function renderCompetitorResultsView(competitorName) {
      if (!currentComp) {
        document.getElementById('competitor-results').innerHTML = '<div>No competition selected</div>';
        return;
      }
      const rounds = await getEventsForComp(currentComp);
      const rowsOut = [];
      for (const r of rounds) {
        const rows = await getRoundData(r.id);
        const match = rows.find(rr => rr.competitor_name === competitorName);
        if (match) {
          const attempts = [
            match.attempt1 === null ? null : Number(match.attempt1),
            match.attempt2 === null ? null : Number(match.attempt2),
            match.attempt3 === null ? null : Number(match.attempt3),
            match.attempt4 === null ? null : Number(match.attempt4),
            match.attempt5 === null ? null : Number(match.attempt5)
          ];
          const stats = computeForFormat(r.format, attempts);
          rowsOut.push({
            round: r,
            attempts,
            best: stats.best,
            average: stats.average
          });
        }
      }

      // render table
      document.getElementById('competitor-title').textContent = competitorName;
      const thead = document.querySelector('#competitor-results thead');
      const tbody = document.querySelector('#competitor-results tbody');
      thead.innerHTML = '';
      tbody.innerHTML = '';
      const headerRow = document.createElement('tr');
      ['Event', 'Round', 'Attempts', 'Best', 'Average'].forEach(h => {
        const th = document.createElement('th'); th.textContent = h; headerRow.appendChild(th);
      });
      thead.appendChild(headerRow);

      rowsOut.forEach(r => {
        const tr = document.createElement('tr');
        const tdEvent = document.createElement('td'); tdEvent.textContent = r.round.event_id; tr.appendChild(tdEvent);
        const tdRound = document.createElement('td'); tdRound.textContent = r.round.round_code; tr.appendChild(tdRound);
        const tdAttempts = document.createElement('td'); tdAttempts.className = 'solves-cell';
        r.attempts.forEach(a => {
          const span = document.createElement('span'); span.className = 'solve'; span.style.marginRight = '6px';
          span.textContent = (a === null || a === 0) ? '' : csToDisplay(a);
          tdAttempts.appendChild(span);
        });
        tr.appendChild(tdAttempts);
        const tdBest = document.createElement('td'); tdBest.textContent = r.best === null ? '' : csToDisplay(r.best); tr.appendChild(tdBest);
        const tdAvg = document.createElement('td'); tdAvg.textContent = r.average === null ? '' : csToDisplay(r.average); tr.appendChild(tdAvg);
        tbody.appendChild(tr);
      });
    }

    // loadEditableSheet: populate editor-panel with inputs for a round
    async function loadEditableSheet(roundId) {
      if (!isLoggedIn) {
        alert('Please sign in to edit live results.');
        return;
      }
      const rows = await getRoundData(roundId);
      const tableHead = document.querySelector('#edit-table thead');
      const tableBody = document.querySelector('#edit-table tbody');
      tableHead.innerHTML = '';
      tableBody.innerHTML = '';

      const headerRow = document.createElement('tr');
      ['Name', 'Attempt1', 'Attempt2', 'Attempt3', 'Attempt4', 'Attempt5'].forEach(h => {
        const th = document.createElement('th'); th.textContent = h; headerRow.appendChild(th);
      });
      tableHead.appendChild(headerRow);

      rows.forEach(row => {
        const tr = document.createElement('tr');
        const tdName = document.createElement('td'); tdName.textContent = row.competitor_name; tr.appendChild(tdName);
        for (let i = 1; i <= 5; i++) {
          const td = document.createElement('td');
          const input = document.createElement('input');
          input.type = 'text';
          input.dataset.rowId = row.id;
          input.dataset.attempt = 'attempt' + i;
          // display current value as seconds string
          const val = row['attempt' + i];
          input.value = (val === null || val === 0) ? '' : csToDisplay(val);
          td.appendChild(input);
          tr.appendChild(td);
        }
        tableBody.appendChild(tr);
      });

      document.getElementById('editor-panel').style.display = 'block';
      document.getElementById('editing-sheet-name').textContent = currentRoundMeta ? `${currentRoundMeta.event_id} ${currentRoundMeta.round_code}` : '';
    }

    // submitEdits: read inputs, convert to centiseconds, and update DB
    async function submitEdits() {
      if (!isLoggedIn) {
        alert('Please sign in to submit edits.');
        return;
      }
      const inputs = Array.from(document.querySelectorAll('#edit-table tbody input'));
      // group by rowId
      const updatesByRow = {};
      inputs.forEach(inp => {
        const rowId = Number(inp.dataset.rowId);
        const attemptKey = inp.dataset.attempt; // e.g., attempt1
        if (!updatesByRow[rowId]) updatesByRow[rowId] = { id: rowId };
        const cs = parseToCentiseconds(inp.value);
        // store null as null, DNF/DNS strings handled by parseToCentiseconds (not DNF/DNS)
        // allow user to type 'DNF' or 'DNS'
        const v = (typeof inp.value === 'string' && inp.value.trim().toUpperCase() === 'DNF') ? -1
                : (typeof inp.value === 'string' && inp.value.trim().toUpperCase() === 'DNS') ? -2
                : (cs === null ? null : cs);
        updatesByRow[rowId][attemptKey] = v;
      });

      // perform updates in a transaction-like loop (Supabase doesn't support multi-row transactions in client)
      const updates = Object.values(updatesByRow);
      const errors = [];
      for (const u of updates) {
        // build update object only with attempt fields
        const payload = {};
        for (let i = 1; i <= 5; i++) {
          const key = 'attempt' + i;
          if (u.hasOwnProperty(key)) payload[key] = u[key];
        }
        const { data, error } = await supabase
          .from('round_results')
          .update(payload)
          .eq('id', u.id);
        if (error) {
          errors.push({ id: u.id, error });
        }
      }

      if (errors.length) {
        console.error('Errors updating rows', errors);
        alert('Some updates failed. Check console for details.');
      } else {
        // hide editor and reload leaderboard
        document.getElementById('editor-panel').style.display = 'none';
        if (currentRound) {
          await loadLeaderboard(currentRound, currentRoundMeta ? currentRoundMeta.format : null);
        }
        document.getElementById('submit-status').textContent = 'Updates saved';
        document.getElementById('submit-status').style.display = 'block';
        setTimeout(()=>{ document.getElementById('submit-status').style.display = 'none'; }, 2000);
      }
    }

    function cancelEdit() {
      document.getElementById('editor-panel').style.display = 'none';
    }

    /************************************************************************
     * Authentication (Firebase used previously). We'll keep Firebase auth
     * for login UI but map to isLoggedIn flag. Supabase anon key is used for DB.
     ************************************************************************/
    const firebaseConfig = {
      apiKey: "AIzaSyDUMb1cAuCzRwDfLipld8YrNX-WG4a7P2s",
      authDomain: "lsccubingclub-ebd29.firebaseapp.com",
      projectId: "lsccubingclub-ebd29",
    };
    firebase.initializeApp(firebaseConfig);
    const auth = firebase.auth();

    auth.onAuthStateChanged(user => {
      isLoggedIn = !!user;
      document.getElementById('login-panel').style.display = isLoggedIn ? 'none' : 'block';
      document.getElementById('login-status').textContent = isLoggedIn ? `Signed in as ${user.email}` : '';
      // show editor if a round is selected
      document.getElementById('editor-panel').style.display = (isLoggedIn && currentRound) ? 'block' : 'none';
    });

    async function login() {
      const email = document.getElementById('email').value;
      const password = document.getElementById('password').value;
      try {
        await auth.signInWithEmailAndPassword(email, password);
      } catch (e) {
        console.error('Login error', e);
        alert('Login failed: ' + (e.message || e));
      }
    }

    /************************************************************************
     * Small helpers to integrate with existing UI navigation
     ************************************************************************/
    function showView(viewId) {
      // hide all main views and show the requested one
      const mains = document.querySelectorAll('main');
      mains.forEach(m => m.hidden = true);
      const el = document.getElementById(viewId);
      if (el) el.hidden = false;
      // adjust header club-name centering for home
      if (viewId === 'view-home') {
        document.getElementById('club-name').style.margin = '0 auto';
      } else {
        document.getElementById('club-name').style.margin = '';
      }
    }

    // wire initial UI actions
    document.addEventListener('DOMContentLoaded', () => {
      // initialize live once when user navigates to live
      // For simplicity, call initLiveOnce immediately so competitions list is ready
      initLiveOnce().catch(e => console.error(e));

      // wire editor open: double-click leaderboard row to open editor for that competitor (if logged in)
      document.querySelector('#leaderboard').addEventListener('dblclick', async (ev) => {
        if (!isLoggedIn) return;
        // open editable sheet for currentRound
        if (currentRound) {
          await loadEditableSheet(currentRound);
          showView('view-live'); // ensure live view visible
        }
      });

      // wire sidebar open/close
      document.getElementById('btn-open-sidebar').onclick = () => {
        document.body.classList.toggle('sidebar-open');
      };
      document.getElementById('sidebar-overlay').onclick = () => {
        document.body.classList.remove('sidebar-open');
      };

      // wire competitions link (if present)
      const compsLink = document.querySelector('a.btn-live[href="/competitions"]');
      if (compsLink) compsLink.onclick = (e) => { e.preventDefault(); showView('view-competitions'); };

      // wire live link
      const liveLink = document.querySelector('a.btn-live[href="/live"]');
      if (liveLink) liveLink.onclick = (e) => { e.preventDefault(); showView('view-live'); };

      // wire podiums and competitors sidebar buttons already set in initLiveOnce
    });