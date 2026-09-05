/* Decorative artwork only. Live state and all server actions remain in app.js. */
"use strict";

(() => {
  const asset = (name) => `/assets/studio/${name}-v1.png`;
  function illustration(name, className = "chapter-art") {
    const figure = document.createElement("figure");
    figure.className = className;
    figure.setAttribute("aria-hidden", "true");
    const image = document.createElement("img");
    image.src = asset(name);
    image.alt = "";
    image.loading = "lazy";
    image.decoding = "async";
    image.width = name === "world" ? 1536 : 1254;
    image.height = name === "world" ? 1024 : 1254;
    // An unavailable decorative asset must never obstruct text or controls.
    image.addEventListener("error", () => figure.classList.add("art-unavailable"), {once: true});
    figure.append(image);
    return figure;
  }

  const chapters = {
    performance: "observatory", console: "observatory", mods: "workshop",
    files: "archive", settings: "workshop", players: "camp", rules: "seed",
    backups: "vault", automation: "clock", diagnostics: "observatory", about: "seed",
  };
  for (const [view, name] of Object.entries(chapters)) {
    const heading = document.querySelector(`[data-view-panel="${view}"] > .view-heading`);
    if (!heading || heading.querySelector(".chapter-art")) continue;
    heading.classList.add("illustrated-heading");
    heading.prepend(illustration(name));
  }

  const empties = {
    "mod-empty": "workshop", "file-empty": "archive", "backup-empty": "vault",
    "crash-empty": "observatory",
  };
  for (const [id, name] of Object.entries(empties)) {
    const empty = document.getElementById(id);
    if (!empty || empty.querySelector("figure")) continue;
    const description = document.createElement("p");
    description.textContent = empty.textContent;
    empty.replaceChildren(illustration(name, "empty-art"), description);
    empty.classList.add("illustrated-empty");
  }

  const features = ["seed", "observatory", "workshop", "vault", "camp", "seed", "world", "clock", "observatory"];
  document.querySelectorAll('[data-view-panel="about"] .feature-card').forEach((card, index) => {
    const oldIcon = card.querySelector(":scope > i");
    if (oldIcon) oldIcon.replaceWith(illustration(features[index], "feature-art"));
  });

  const importHeading = document.querySelector("#import-modal .modal-heading");
  if (importHeading && !importHeading.querySelector(".import-art")) {
    importHeading.prepend(illustration("seed", "import-art"));
  }
})();
