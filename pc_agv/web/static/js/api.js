// Chamadas a API do AGV.

export async function postJson(url, payload, signal) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload || {}),
    signal: signal || null,
  });
  return response.json();
}
