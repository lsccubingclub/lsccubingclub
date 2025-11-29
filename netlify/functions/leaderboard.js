// netlify/functions/leaderboard.js
const { createClient } = require('@supabase/supabase-js');

const SUPABASE_URL = process.env.SUPABASE_URL;
const SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY;

if (!SUPABASE_URL || !SUPABASE_SERVICE_ROLE_KEY) {
  console.error('Missing Supabase env vars');
}

const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, {
  auth: { persistSession: false }
});

// Helper to compute best/average server-side (same logic as client)
function computeBestAndAverage(row, format) {
  const attempts = [row.attempt1, row.attempt2, row.attempt3, row.attempt4, row.attempt5].map(v => {
    if (v === null || v === undefined || v === 0 || v === '') return null;
    const n = Number(v);
    if (!Number.isFinite(n)) return null;
    return Math.trunc(n);
  });

  const valid = attempts.filter(a => a !== null && a !== -2);

  if (String(format || '').toLowerCase().includes('fmc')) {
    const best = valid.length ? Math.min(...valid) : null;
    return { best, average: null };
  }

  if (format === 'bo1' || format === 'bo2' || format === 'bo3' || format === 'mo3') {
    const best = valid.length ? Math.min(...valid) : null;
    let average = null;
    if (format === 'mo3' && valid.length === 3) {
      average = Math.round(valid.reduce((s, x) => s + x, 0) / 3);
    }
    return { best, average };
  }

  if (format === 'ao5') {
    if (valid.length < 5) {
      const best = valid.length ? Math.min(...valid) : null;
      return { best, average: null };
    }
    const hasDNF = attempts.some(a => a === -1);
    const numericAttempts = attempts.map(a => (a === -1 ? null : a));
    const best = Math.min(...attempts.filter(a => a !== null && a !== -1));
    if (hasDNF) {
      return { best, average: -1 };
    }
    const sorted = numericAttempts.slice().sort((a,b)=>a-b);
    const middle = sorted.slice(1,4);
    const avg = Math.round(middle.reduce((s,x)=>s+x,0) / 3);
    return { best, average: avg };
  }

  const best = valid.length ? Math.min(...valid) : null;
  return { best, average: null };
}

exports.handler = async function(event, context) {
  try {
    const params = event.queryStringParameters || {};
    const round_id = params.round_id ? Number(params.round_id) : null;
    const sort_by = params.sort_by || 'best';
    const limit = params.limit ? Number(params.limit) : null;

    if (!round_id) {
      return { statusCode: 400, body: 'round_id required' };
    }

    // Fetch round metadata (format) and rows
    const { data: roundData, error: roundErr } = await supabase
      .from('rounds')
      .select('*')
      .eq('id', round_id)
      .limit(1)
      .single();

    if (roundErr) {
      return { statusCode: 500, body: 'Failed to load round metadata: ' + roundErr.message };
    }

    const format = roundData.format || 'ao5';

    const { data: rows, error: rowsErr } = await supabase
      .from('round_results')
      .select('*')
      .eq('round_id', round_id);

    if (rowsErr) {
      return { statusCode: 500, body: 'Failed to load round results: ' + rowsErr.message };
    }

    // Compute best/average for each row
    const computed = rows.map(r => {
      const ca = computeBestAndAverage(r, format);
      return Object.assign({}, r, { best: ca.best, average: ca.average });
    });

    // Compute ranks (by best and by average). Nulls and -1 (DNF) handled: DNF -> worst rank
    function rankBy(field) {
      const arr = computed.slice().map(r => ({ id: r.id, val: r[field] }));
      // sort: null/undefined -> Infinity, -1 (DNF) -> Infinity for average; for best smaller is better
      arr.sort((a,b) => {
        const va = a.val;
        const vb = b.val;
        const aInf = (va === null || va === undefined || va === -1);
        const bInf = (vb === null || vb === undefined || vb === -1);
        if (aInf && bInf) return 0;
        if (aInf) return 1;
        if (bInf) return -1;
        return va - vb;
      });
      const ranks = {};
      let rank = 1;
      for (let i=0;i<arr.length;i++) {
        if (i>0 && arr[i].val !== arr[i-1].val) rank = i+1;
        ranks[arr[i].id] = rank;
      }
      return ranks;
    }

    const ranksBest = rankBy('best');
    const ranksAvg = rankBy('average');

    const finalRows = computed.map(r => ({
      ...r,
      rank_by_best: ranksBest[r.id] || null,
      rank_by_average: ranksAvg[r.id] || null
    }));

    // Optionally sort by requested field
    if (sort_by === 'average') {
      finalRows.sort((a,b) => {
        const av = a.average, bv = b.average;
        if (av === null || av === undefined || av === -1) return 1;
        if (bv === null || bv === undefined || bv === -1) return -1;
        return av - bv;
      });
    } else {
      finalRows.sort((a,b) => {
        const av = a.best, bv = b.best;
        if (av === null || av === undefined || av === -1) return 1;
        if (bv === null || bv === undefined || bv === -1) return -1;
        return av - bv;
      });
    }

    const limited = limit ? finalRows.slice(0, limit) : finalRows;

    return {
      statusCode: 200,
      body: JSON.stringify({
        round: roundData,
        rows: limited
      }),
      headers: { 'Content-Type': 'application/json' }
    };
  } catch (err) {
    console.error(err);
    return { statusCode: 500, body: String(err) };
  }
};
