// Small shared helpers for the Financial Email Tracker web UI.

if (window.htmx) {
    htmx.config.defaultSwapStyle = "outerHTML";
}

/**
 * Fetch wrapper used by inline onclick handlers throughout the templates.
 * Sends JSON when a body is provided, alerts on non-2xx responses, and
 * returns parsed JSON (or null for 204 No Content).
 */
async function apiCall(url, method, body) {
    const res = await fetch(url, {
        method,
        headers: body ? { "Content-Type": "application/json" } : {},
        body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) {
        alert("Request failed: " + res.status);
        throw new Error("failed");
    }
    return res.status === 204 ? null : res.json();
}
