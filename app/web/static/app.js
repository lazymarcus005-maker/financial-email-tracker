// HTMX configuration for the Financial Email Tracker web UI.

if (window.htmx) {
    htmx.config.defaultSwapStyle = "outerHTML";
}

function isInteractiveElement(element) {
    return Boolean(
        element.closest(
            'a, button, input, select, textarea, label, [hx-get], [hx-post], [hx-patch], [hx-delete], [data-no-row-link]'
        )
    );
}

document.addEventListener("click", function (event) {
    const row = event.target.closest("tr[data-href]");
    if (!row || isInteractiveElement(event.target)) {
        return;
    }
    window.location.href = row.dataset.href;
});

document.addEventListener("keydown", function (event) {
    if (event.key !== "Enter" && event.key !== " ") {
        return;
    }
    const row = event.target.closest("tr[data-href]");
    if (!row || isInteractiveElement(event.target)) {
        return;
    }
    event.preventDefault();
    window.location.href = row.dataset.href;
});

function closeModal() {
    var root = document.getElementById("modal-root");
    if (root) root.innerHTML = "";
}

document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") closeModal();
});
