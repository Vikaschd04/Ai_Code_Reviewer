import { BaseView, type Renderable } from "./base";
import missing from "./missing";

export class Home extends BaseView implements Renderable {
  render(): string {
    return String(missing);
  }
}
