const controls = document.querySelector("#scene-controls");
const sceneImage = document.querySelector("#scene-image");
const sceneNote = document.querySelector("#scene-note");
const sceneButtons = [...controls.querySelectorAll("button")];
let requestedScene = 0;

controls.hidden = false;

for (const button of sceneButtons) {
  button.addEventListener("click", async () => {
    const request = ++requestedScene;
    const { image, alt, note } = button.dataset;
    const preview = new Image();
    preview.src = `./screenshots/${image}`;

    try {
      await preview.decode();
    } catch {
      if (request === requestedScene) {
        sceneNote.textContent = "这张窗景暂时无法加载，请重试。";
      }
      return;
    }

    if (request !== requestedScene) return;

    sceneImage.src = preview.src;
    sceneImage.alt = alt;
    sceneNote.textContent = note;
    for (const scene of sceneButtons) {
      scene.setAttribute("aria-pressed", String(scene === button));
    }
  });
}
