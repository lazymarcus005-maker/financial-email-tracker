// HTMX configuration for the Financial Email Tracker web UI.

if (window.htmx) {
    htmx.config.defaultSwapStyle = "outerHTML";
}

function closeModal() {
    var root = document.getElementById("modal-root");
    if (root) root.innerHTML = "";
}

document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") closeModal();
});
