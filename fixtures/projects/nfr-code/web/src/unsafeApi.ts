import axios from "axios";

export const orders = axios.create({ baseURL: "https://orders.internal" });
export const plain = axios.create();
