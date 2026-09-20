/**
 * The allergies the app watches for, for a sighted helper.
 *
 * A pill at the top right opens a sheet that rises from the bottom: the
 * allergens on file as chips (each removable), a field to add one, quick
 * buttons for the common groups, the wearer's name, the doctor's address,
 * the state of the mail setup, and a button that sends a test email through
 * the same path an alert takes. The blind user hears every change, because
 * what the scanner watches for is a state they cannot see, and the one that
 * decides whether a doctor gets email.
 */

import { attachSheet } from "./sheet";

type ProfileInfo = { name: string; allergens: string[]; doctor_email: string };
type MailInfo = { configured: boolean; note: string; to: string };
type ProfileState = { enabled: boolean; profile: ProfileInfo; known_allergens: string[]; mail: MailInfo };
type TestResult = { sent: boolean; to: string; path: string | null; error: string | null; spoken: string };

const EMPTY: ProfileState = {
  enabled: true,
  profile: { name: "", allergens: [], doctor_email: "" },
  known_allergens: [],
  mail: { configured: false, note: "", to: "" },
};

const el = <T extends HTMLElement>(id: string): T => document.getElementById(id) as T;

/** "peanut", "peanut and dairy", "peanut, dairy and egg". */
function listed(items: string[]): string {
  if (items.length === 0) return "nothing";
  if (items.length === 1) return items[0];
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

export function initAllergies(options: {
  speak: (text: string) => void;
  /** Called when the sheet opens, so another sheet can close. */
  onOpen?: () => void;
}) {
  const handle = el<HTMLButtonElement>("allergies-handle");
  const panel = el<HTMLElement>("allergies-panel");
  const closeButton = el<HTMLButtonElement>("allergies-close");
  const testButton = el<HTMLButtonElement>("allergies-test");
  const banner = el<HTMLDivElement>("allergies-banner");
  const list = el<HTMLDivElement>("allergies-list");
  const addInput = el<HTMLInputElement>("allergies-add");
  const addButton = el<HTMLButtonElement>("allergies-add-button");
  const quick = el<HTMLDivElement>("allergies-quick");
  const nameInput = el<HTMLInputElement>("allergies-name");
  const doctorInput = el<HTMLInputElement>("allergies-doctor");
  const mailLine = el<HTMLParagraphElement>("allergies-mail");

  let state: ProfileState = EMPTY;
  let announced = false;
  let lastAlert: { allergens: string[]; emailed: boolean } | null = null;

  const sheet = attachSheet({
    handle,
    panel,
    close: closeButton,
    onOpen: () => options.onOpen?.(),
    onChange: (open) => {
      if (open) {
        lastAlert = null;
        void refresh();
      }
    },
  });

  // Keys typed into the sheet are the sheet's: everywhere else "p", "b",
  // "l", the digits and the letters drive the app.
  panel.addEventListener("keydown", (e) => e.stopPropagation());

  async function request<T>(method: string, path: string, payload?: unknown): Promise<T | null> {
    try {
      const response = await fetch(path, {
        method,
        headers: payload === undefined ? {} : { "Content-Type": "application/json" },
        body: payload === undefined ? undefined : JSON.stringify(payload),
        cache: "no-store",
      });
      if (!response.ok) return null;
      return (await response.json()) as T;
    } catch {
      return null;
    }
  }

  async function refresh(): Promise<void> {
    const fetched = await request<ProfileState>("GET", "/profile");
    if (fetched) state = fetched;
    render(fetched === null);
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

  /** The pill: the last alert until the sheet is opened, else the count. */
  function renderHandle(): void {
    if (lastAlert) {
      handle.textContent = `${lastAlert.emailed ? "Alert" : "Warning"}: ${listed(lastAlert.allergens)}`;
      handle.dataset.tone = "alert";
      return;
    }
    const count = state.profile.allergens.length;
    handle.textContent = count === 0 ? "Allergies" : `Allergies · ${count}`;
    handle.dataset.tone = count === 0 ? "" : "ok";
  }

  function renderBanner(unreachable: boolean): void {
    delete banner.dataset.tone;
    if (unreachable) {
      banner.textContent = "Couldn't reach the backend to read the profile.";
      return;
    }
    const { allergens } = state.profile;
    if (allergens.length === 0) {
      banner.textContent = "No allergies on file, so the scanner is idle. Add one below.";
      return;
    }
    banner.dataset.tone = "ok";
    const to = state.mail.to || "the doctor (no address yet)";
    banner.textContent =
      `Watching labels and barcodes for ${listed(allergens)}. A confirmed one sounds a warning and emails ${to}; ` +
      `"may contain" and a food seen in view only warn. Say "Jarvis, false alarm" to send a correction.`;
  }

  function chip(allergen: string): HTMLElement {
    const node = document.createElement("span");
    node.className = "chip";
    node.textContent = allergen;
    const remove = button("×", "x", () => removeAllergen(allergen));
    remove.setAttribute("aria-label", `Remove ${allergen}`);
    node.append(remove);
    return node;
  }

  function renderQuick(): void {
    quick.replaceChildren();
    const missing = state.known_allergens.filter((a) => !state.profile.allergens.includes(a));
    if (missing.length === 0) return;
    const label = document.createElement("span");
    label.className = "meta";
    label.textContent = "Quick add:";
    quick.append(label);
    for (const allergen of missing) quick.append(button(allergen, "small quick", () => addAllergen(allergen)));
  }

  function render(unreachable: boolean): void {
    renderHandle();
    if (!sheet.isOpen) return;
    renderBanner(unreachable);
    list.replaceChildren(...state.profile.allergens.map(chip));
    if (state.profile.allergens.length === 0) {
      const empty = document.createElement("span");
      empty.className = "meta";
      empty.textContent = "none yet";
      list.append(empty);
    }
    renderQuick();
    if (document.activeElement !== nameInput) nameInput.value = state.profile.name;
    if (document.activeElement !== doctorInput) doctorInput.value = state.profile.doctor_email;
    mailLine.textContent = state.mail.note;
    testButton.disabled = !state.mail.to;
  }

  /** Save some fields; speak what the server now holds, since it
   * normalizes ("Peanuts" becomes "peanut"). */
  async function save(fields: Partial<ProfileInfo>, spoken: (profile: ProfileInfo) => string): Promise<void> {
    const updated = await request<ProfileState>("POST", "/profile", fields);
    if (!updated) {
      options.speak("Couldn't save the profile; the backend did not answer.");
      return;
    }
    state = updated;
    render(false);
    options.speak(spoken(state.profile));
  }

  async function addAllergen(raw: string): Promise<void> {
    const value = raw.trim().toLowerCase();
    if (!value) return;
    if (state.profile.allergens.includes(value)) {
      options.speak(`${value} is already on the list.`);
      return;
    }
    addInput.value = "";
    await save({ allergens: [...state.profile.allergens, value] }, (profile) =>
      `Added ${value}. Your allergies are ${listed(profile.allergens)}.`
    );
  }

  async function removeAllergen(allergen: string): Promise<void> {
    await save({ allergens: state.profile.allergens.filter((a) => a !== allergen) }, (profile) =>
      profile.allergens.length > 0
        ? `Removed ${allergen}. Your allergies are ${listed(profile.allergens)}.`
        : `Removed ${allergen}. No allergies on file now, so the scanner is idle.`
    );
  }

  addButton.addEventListener("click", (e) => {
    e.stopPropagation();
    void addAllergen(addInput.value);
  });
  addInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      void addAllergen(addInput.value);
    }
  });
  nameInput.addEventListener("change", () => {
    const name = nameInput.value.trim();
    if (name === state.profile.name) return;
    void save({ name }, (profile) =>
      profile.name ? `The wearer's name is now ${profile.name}.` : "The wearer's name is cleared."
    );
  });
  doctorInput.addEventListener("change", () => {
    const email = doctorInput.value.trim();
    if (email === state.profile.doctor_email) return;
    void save({ doctor_email: email }, (profile) =>
      profile.doctor_email
        ? `Alerts will go to ${profile.doctor_email}.`
        : "The doctor's address is cleared; alerts will only be written to the outbox."
    );
  });
  testButton.addEventListener("click", async (e) => {
    e.stopPropagation();
    testButton.disabled = true;
    options.speak("Sending a test email.");
    const result = await request<TestResult>("POST", "/alerts/test");
    testButton.disabled = false;
    if (!result) {
      options.speak("The test email could not be started; the backend did not answer.");
      return;
    }
    options.speak(result.spoken + (result.sent ? ` It went to ${result.to}.` : ""));
    if (result.error) banner.textContent = `Not sent: ${result.error}`;
    else if (result.path && !result.sent) banner.textContent = `Written to ${result.path}`;
  });

  return {
    /** Show the pill once the app is running. */
    reveal(): void {
      handle.hidden = false;
      renderHandle();
      void refresh();
    },
    toggle: sheet.toggle,
    close: sheet.close,
    get isOpen(): boolean {
      return sheet.isOpen;
    },
    /** The server's `ready` says what is watched for; spoken once a session. */
    onReady(allergens: string[]): void {
      if (announced) return;
      announced = true;
      if (allergens.length > 0) options.speak(`Allergy scanner on for ${listed(allergens)}.`);
    },
    /** An alert or a warning arrived: the pill says so until the sheet opens. */
    onAlert(allergens: string[], emailed: boolean): void {
      lastAlert = { allergens, emailed };
      renderHandle();
    },
  };
}
