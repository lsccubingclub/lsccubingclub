// netlify/functions/write.js
const { createClient } = require('@supabase/supabase-js');

exports.handler = async function(event) {
  if (event.httpMethod !== 'POST') return { statusCode: 405, body: 'Method not allowed' };
  const SUPABASE_URL = process.env.SUPABASE_URL;
  const SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!SUPABASE_SERVICE_ROLE_KEY) return { statusCode: 500, body: 'Missing service role key' };

  const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY);
  const body = JSON.parse(event.body || '{}');
  const { round_id, updates } = body;
  if (!round_id || !Array.isArray(updates)) return { statusCode: 400, body: 'Invalid payload' };

  // perform updates in a transaction
  try {
    for (const u of updates) {
      const id = u.id;
      const payload = {
        attempt1: u.attempt1 ?? null,
        attempt2: u.attempt2 ?? null,
        attempt3: u.attempt3 ?? null,
        attempt4: u.attempt4 ?? null,
        attempt5: u.attempt5 ?? null
      };
      await supabase.from('round_results').update(payload).eq('id', id);
    }

    // call stored procedure to recompute best/average and ranks for this round
    await supabase.rpc('compute_round_results', { p_round_id: round_id });

    return { statusCode: 200, body: 'OK' };
  } catch (err) {
    console.error(err);
    return { statusCode: 500, body: JSON.stringify(err) };
  }
};
