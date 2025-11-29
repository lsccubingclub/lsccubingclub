// netlify/functions/update.js
const { createClient } = require('@supabase/supabase-js');

const SUPABASE_URL = process.env.SUPABASE_URL;
const SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY;

const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, {
  auth: { persistSession: false }
});

exports.handler = async function(event, context) {
  try {
    if (event.httpMethod !== 'POST') {
      return { statusCode: 405, body: 'Method not allowed' };
    }
    const body = JSON.parse(event.body || '{}');
    const action = body.action;
    const payload = body.payload || {};

    if (action === 'update_row') {
      // payload must include id and round_id
      const id = Number(payload.id);
      if (!id) return { statusCode: 400, body: 'id required' };

      // Build update object only with attempt fields present
      const updateObj = {};
      for (let i=1;i<=5;i++) {
        if (payload.hasOwnProperty('attempt' + i)) {
          const v = payload['attempt' + i];
          updateObj['attempt' + i] = v === null ? null : Number(v);
        }
      }

      const { data, error } = await supabase
        .from('round_results')
        .update(updateObj)
        .eq('id', id)
        .select()
        .single();

      if (error) {
        return { statusCode: 500, body: 'Update failed: ' + error.message };
      }

      return { statusCode: 200, body: JSON.stringify({ success: true, row: data }), headers: { 'Content-Type': 'application/json' } };
    }

    return { statusCode: 400, body: 'Unknown action' };
  } catch (err) {
    console.error(err);
    return { statusCode: 500, body: String(err) };
  }
};
