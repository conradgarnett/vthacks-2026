/**
 * The places the app remembers, for a sighted helper.
 *
 * A pill at the top right opens a sheet that rises from the bottom: every
 * remembered place with its views, each place renamable, deletable and
 * linkable into another, each view deletable on its own. The blind user
 * hears the recognition in the scan itself ("It looks like you are in
 * Hallway 1"); nothing here is load-bearing. But every edit made here is
 * spoken, because a memory that changed is a state the user cannot see.
 */

import type { PlaceEvent } from "./ws";

type SceneInfo = {
  id: string;
  at: number;
  labels: Record<string, number>;
  words: string[];
  room: string | null;
  thumbnail: string | null;
};
type PlaceInfo = { id: string; name: string; kind: string | null; created_at: number; scenes: SceneInfo[] };
type Memory = {
  enabled: boolean;
  places: PlaceInfo[];
  unplaced: SceneInfo[];
  current: { place_id: string; name: string; score: number; age_s: number; fresh: boolean } | null;
};

const EMPTY: Memory = { enabled: true, places: [], unplaced: [], current: null };
// A delete needs a second tap within this long; the first tap only arms it.
const ARM_MS = 3000;

const el = <T extends HTMLElement>(id: string): T => document.getElementById(id) as T;

export function initPlaces(options: {
  speak: (text: string) => void;
  /** Called with the memory whenever it is fetched or edited. */
  onMemory?: (memory: Memory) => void;
  /** Called when the sheet opens, so another sheet can close. */
  onOpen?: () => void;
}) {
  const handle = el<HTMLButtonElement>("places-handle");
  const panel = el<HTMLElement>("places-panel");
  const banner = el<HTMLDivElement>("places-banner");
  const list = el<HTMLDivElement>("places-list");
  const closeButton = el<HTMLButtonElement>("places-close");
  const forgetButton = el<HTMLButtonElement>("places-forget");

  let memory: Memory = EMPTY;
  let lastEvent: PlaceEvent | null = null;
  let open = false;

  const percent = (score: number): string => `${Math.round(score * 100)}%`;

  async function request(method: string, path: string, payload?: unknown): Promise<Memory | null> {
    try {
      const response = await fetch(path, {
        method,
        headers: payload === undefined ? {} : { "Content-Type": "application/json" },
        body: payload === undefined ? undefined : JSON.stringify(payload),
        cache: "no-store",
      });
      if (!response.ok) return null;
      return (await response.json()) as Memory;
    } catch {
      return null;
    }
  }

  async function refresh(): Promise<void> {
    const fetched = await request("GET", "/places");
    if (fetched) memory = fetched;
    render(fetched === null);
  }

  function setOpen(next: boolean): void {
    open = next;
    panel.dataset.open = String(next);
    handle.setAttribute("aria-expanded", String(next));
    if (next) {
      options.onOpen?.();
      void refresh();
    }
  }

  /** The pill's text: where the user is, or how many places are known. */
  function renderHandle(): void {
    const current = memory.current;
    if (current && current.fresh) {
      handle.textContent = `In ${current.name}`;
      handle.dataset.tone = "ok";
    } else if (lastEvent?.kind === "new" && lastEvent.place) {
      handle.textContent = `New: ${lastEvent.place.name}`;
      handle.dataset.tone = "new";
    } else {
      const count = memory.places.length;
      handle.textContent = count === 0 ? "Places" : `Places · ${count}`;
      handle.dataset.tone = "";
    }
  }

  function renderBanner(unreachable: boolean): void {
    banner.replaceChildren();
    delete banner.dataset.tone;
    if (unreachable) {
      banner.textContent = "Couldn't reach the backend to list the places.";
      return;
    }
    const event = lastEvent;
    const current = memory.current;
    if (event?.kind === "recognized" && event.place) {
      banner.dataset.tone = "ok";
      banner.textContent = `You seem to be in ${event.place.name} (${percent(event.score)} match; ${event.evidence ?? ""}).`;
    } else if (event?.kind === "new" && event.place) {
      banner.dataset.tone = "new";
      banner.textContent = `New place remembered as ${event.place.name}.`;
    } else if (event?.kind === "unsure" && event.candidate && event.scene_id) {
      banner.dataset.tone = "unsure";
      const candidate = event.candidate;
      const sceneId = event.scene_id;
      banner.append(
        `Not sure. This might be ${candidate.name} (${percent(event.score)}; ${event.evidence ?? ""}). `
      );
      banner.append(
        button("Yes, link it", "small primary", async () => {
          const updated = await request("POST", `/scenes/${sceneId}/place`, { place_id: candidate.id });
          if (updated) {
            memory = updated;
            lastEvent = null;
            options.speak(`Linked this view into ${candidate.name}.`);
            render(false);
          }
        }),
        " ",
        button("No, a new place", "small", async () => {
          const updated = await request("POST", `/scenes/${sceneId}/place`, { place_id: null });
          if (updated) {
            memory = updated;
            const created = updated.places[updated.places.length - 1];
            lastEvent = null;
            options.speak(`Remembered as ${created?.name ?? "a new place"}.`);
            render(false);
          }
        })
      );
    } else if (event?.kind === "skipped") {
      banner.textContent = "Nothing in that scan was worth remembering as a place.";
    } else if (current && current.fresh) {
      banner.dataset.tone = "ok";
      banner.textContent = `Last recognized: ${current.name} (${percent(current.score)}), ${Math.round(current.age_s)} s ago.`;
    } else if (memory.places.length === 0) {
      banner.textContent = "Nothing remembered yet. Scan somewhere and it will be remembered as a place.";
    } else {
      banner.textContent = "Scan to find out where you are.";
    }
  }

  function button(label: string, className: string, onClick: () => void | Promise<void>): HTMLButtonElement {
    const node = document.createElement("button");
    node.type = "button";
    node.className = className;
    node.textContent = label;
    node.addEventListener("click", (e) => {
      e.stopPropagation();
      void onClick();
    });
    return node;
  }

  /** A delete that needs a second tap: the first arms it for a moment. */
  function armedButton(label: string, className: string, onConfirm: () => void | Promise<void>): HTMLButtonElement {
    let armed: number | null = null;
    const node = button(label, className, async () => {
      if (armed === null) {
        node.dataset.armed = "true";
        node.textContent = "Sure?";
        armed = window.setTimeout(() => {
          armed = null;
          delete node.dataset.armed;
          node.textContent = label;
        }, ARM_MS);
        return;
      }
      window.clearTimeout(armed);
      armed = null;
      await onConfirm();
    });
    return node;
  }

  function describeScene(scene: SceneInfo): string {
    const things = Object.entries(scene.labels)
      .map(([label, count]) => (count > 1 ? `${label} ×${count}` : label))
      .join(", ");
    const parts = [things || "nothing recognized"];
    if (scene.words.length) parts.push(`words: ${scene.words.join(", ")}`);
    return parts.join(" · ");
  }

  function renderView(scene: SceneInfo, holder: PlaceInfo | null): HTMLElement {
    const node = document.createElement("div");
    node.className = "view";
    node.title = describeScene(scene);
    const image = document.createElement("img");
    image.alt = describeScene(scene);
    if (scene.thumbnail) image.src = scene.thumbnail;
    node.append(image);
    node.append(
      button("×", "x", async () => {
        const updated = await request("DELETE", `/scenes/${scene.id}`);
        if (!updated) return;
        memory = updated;
        options.speak(holder ? `Forgot one view of ${holder.name}.` : "Forgot that view.");
        render(false);
      })
    );
    node.lastElementChild?.setAttribute("aria-label", "Forget this view");
    return node;
  }

  function linkSelect(placeholder: string, exclude: string | null, onPick: (placeId: string | null) => void): HTMLSelectElement {
    const select = document.createElement("select");
    select.setAttribute("aria-label", placeholder);
    const head = document.createElement("option");
    head.value = "";
    head.textContent = placeholder;
    select.append(head);
    for (const place of memory.places) {
      if (place.id === exclude) continue;
      const option = document.createElement("option");
      option.value = place.id;
      option.textContent = place.name;
      select.append(option);
    }
    if (exclude === null) {
      const fresh = document.createElement("option");
      fresh.value = "__new__";
      fresh.textContent = "A new place";
      select.append(fresh);
    }
    select.addEventListener("click", (e) => e.stopPropagation());
    select.addEventListener("change", () => {
      const value = select.value;
      select.value = "";
      if (!value) return;
      onPick(value === "__new__" ? null : value);
    });
    return select;
  }

  function renderPlace(place: PlaceInfo): HTMLElement {
    const card = document.createElement("section");
    card.className = "place";
    if (memory.current?.fresh && memory.current.place_id === place.id) card.classList.add("current");

    const row = document.createElement("div");
    row.className = "row";
    const name = document.createElement("input");
    name.className = "name";
    name.value = place.name;
    name.setAttribute("aria-label", `Name of ${place.name}; edit to rename`);
    name.addEventListener("click", (e) => e.stopPropagation());
    name.addEventListener("keydown", (e) => {
      e.stopPropagation();
      if (e.key === "Enter") name.blur();
      if (e.key === "Escape") {
        name.value = place.name;
        name.blur();
      }
    });
    name.addEventListener("change", async () => {
      const next = name.value.trim();
      if (!next || next === place.name) {
        name.value = place.name;
        return;
      }
      const updated = await request("POST", `/places/${place.id}`, { name: next });
      if (!updated) return;
      memory = updated;
      options.speak(`${place.name} is now called ${next}.`);
      render(false);
    });
    const meta = document.createElement("span");
    meta.className = "meta";
    const views = place.scenes.length;
    meta.textContent = `${place.kind ?? "place"} · ${views} ${views === 1 ? "view" : "views"}`;
    row.append(name, meta);

    if (memory.places.length > 1) {
      row.append(
        linkSelect("Link into…", place.id, async (into) => {
          if (!into) return;
          const target = memory.places.find((p) => p.id === into);
          const updated = await request("POST", `/places/${place.id}/merge`, { into });
          if (!updated) return;
          memory = updated;
          options.speak(`Linked ${place.name} into ${target?.name ?? "another place"}.`);
          render(false);
        })
      );
    }
    row.append(
      armedButton("Delete", "small danger", async () => {
        const updated = await request("DELETE", `/places/${place.id}`);
        if (!updated) return;
        memory = updated;
        options.speak(`Forgot ${place.name}.`);
        render(false);
      })
    );
    card.append(row);

    const strip = document.createElement("div");
    strip.className = "views";
    for (const scene of place.scenes) strip.append(renderView(scene, place));
    card.append(strip);
    return card;
  }

  function renderUnplaced(): HTMLElement | null {
    if (memory.unplaced.length === 0) return null;
    const card = document.createElement("section");
    card.className = "place unplaced";
    const title = document.createElement("div");
    title.className = "row";
    const label = document.createElement("span");
    label.className = "name";
    label.textContent = "Views not linked to a place yet";
    title.append(label);
    card.append(title);
    for (const scene of memory.unplaced) {
      const row = document.createElement("div");
      row.className = "row";
      row.append(renderView(scene, null));
      const meta = document.createElement("span");
      meta.className = "meta";
      meta.textContent = describeScene(scene);
      row.append(meta);
      row.append(
        linkSelect("Link into…", null, async (placeId) => {
          const target = placeId ? memory.places.find((p) => p.id === placeId) : null;
          const updated = await request("POST", `/scenes/${scene.id}/place`, { place_id: placeId });
          if (!updated) return;
          memory = updated;
          if (target) options.speak(`Linked this view into ${target.name}.`);
          else options.speak(`Remembered as ${updated.places[updated.places.length - 1]?.name ?? "a new place"}.`);
          render(false);
        })
      );
      card.append(row);
    }
    return card;
  }

  function render(unreachable: boolean): void {
    renderHandle();
    options.onMemory?.(memory);
    if (!open) return;
    renderBanner(unreachable);
    list.replaceChildren();
    const unplaced = renderUnplaced();
    if (unplaced) list.append(unplaced);
    for (const place of memory.places) list.append(renderPlace(place));
    if (memory.places.length === 0 && !unplaced) {
      const empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = "No places yet.";
      list.append(empty);
    }
    forgetButton.hidden = memory.places.length === 0 && memory.unplaced.length === 0;
  }

  handle.addEventListener("click", (e) => {
    e.stopPropagation();
    setOpen(!open);
  });
  closeButton.addEventListener("click", (e) => {
    e.stopPropagation();
    setOpen(false);
  });
  panel.addEventListener("click", (e) => e.stopPropagation());
  forgetButton.replaceWith(
    (() => {
      const node = armedButton("Forget all", "small danger", async () => {
        const updated = await request("DELETE", "/places");
        if (!updated) return;
        memory = updated;
        lastEvent = null;
        options.speak("Forgot every place.");
        render(false);
      });
      node.id = "places-forget";
      return node;
    })()
  );

  return {
    /** Show the pill once the app is running. */
    reveal(): void {
      handle.hidden = false;
      renderHandle();
      void refresh();
    },
    toggle(): void {
      setOpen(!open);
    },
    close(): void {
      setOpen(false);
    },
    get isOpen(): boolean {
      return open;
    },
    /** What the memory made of the latest scan. */
    onEvent(event: PlaceEvent): void {
      lastEvent = event;
      void refresh();
    },
  };
}
