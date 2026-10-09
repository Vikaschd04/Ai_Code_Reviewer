import { LightningElement } from "lwc";
import { items } from "c/cartStore";

export default class CartPanel extends LightningElement {
  items = items;
}
