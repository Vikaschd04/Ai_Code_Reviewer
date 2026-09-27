export function renderGreeting(target: HTMLElement, name: string): void {
  target.innerHTML = "<p>Hello " + name + "</p>";
}

export function renderStatic(target: HTMLElement): void {
  target.innerHTML = "<p>Hello</p>";
  target.textContent = "Hello";
}

export function legacyBanner(message: string): void {
  document.write(message);
}
