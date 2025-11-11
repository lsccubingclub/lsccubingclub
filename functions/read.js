// functions/read.js
export async function handler(event) {
  const APPS_SCRIPT_URL = 'https://script.google.com/macros/s/AKfycbzN3dtl3YEZKK1P7ndYr825xwfEqwVT_4ahjI_WcVFjXY6WRzcAgK4nBdeY_91vfoO2/exec';

  try {
    const params = event.queryStringParameters || {};
    const comp    = params.comp;
    const round   = params.round;
    const format  = params.format || 'json';
    const isPublic = params.public === 'true';

    if (!comp || !round) {
      return {
        statusCode: 400,
        body: JSON.stringify({ error: 'Missing comp or round' })
      };
    }

    const url = `${APPS_SCRIPT_URL}?mode=read&comp=${encodeURIComponent(comp)}&round=${encodeURIComponent(round)}&format=${encodeURIComponent(format)}&public=${isPublic}`;
    const resp = await fetch(url, { method: 'GET', redirect: 'follow' });
    const text = await resp.text();
    const contentType = resp.headers.get('content-type') || 'text/plain';

    return {
      statusCode: resp.status,
      headers: { 'Content-Type': contentType },
      body: text
    };
  } catch (err) {
    return {
      statusCode: 500,
      body: JSON.stringify({ error: err.message })
    };
  }
}
