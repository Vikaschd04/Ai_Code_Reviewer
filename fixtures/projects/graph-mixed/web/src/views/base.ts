export class BaseView {
  render(): string {
    return "";
  }
}

export interface Renderable {
  render(): string;
}
