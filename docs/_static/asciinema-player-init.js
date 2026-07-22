const asciinemaDocsRoot = new URL("../", document.currentScript.src);

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".asciinema-player-embed").forEach((container) => {
    const source = new URL(container.dataset.castSrc, asciinemaDocsRoot).href;
    const options = JSON.parse(container.dataset.playerOptions ?? "{}");

    container.replaceChildren();
    window.AsciinemaPlayer.create(source, container, options);
  });
});
