// netlify/functions/read.js
const { createClient } = require('@supabase/supabase-js');

exports.handler = async function(event) {
  const SUPABASE_URL = process.env.SUPABASE_URL;
  const SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY; // optional
  const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY);

  // Example: read round results by round_id query param
  const roundId = event.queryStringParameters && event.queryStringParameters.round_id;
  if (!roundId) return { statusCode: 400, body: 'round_id required' };

  const { data, error } = await supabase
    .from('round_results')
    .select('*')
    .eq('round_id', roundId);

  if (error) return { statusCode: 500, body: JSON.stringify(error) };
  return { statusCode: 200, body: JSON.stringify(data) };
};
