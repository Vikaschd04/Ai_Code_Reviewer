import axios from "axios";

export const orders = axios.create({ baseURL: "https://orders.internal", timeout: 5000 });
