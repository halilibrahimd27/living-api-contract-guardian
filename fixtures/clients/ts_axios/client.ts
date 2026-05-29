// Fixture: a TypeScript axios client used by the miner test corpus.

import axios from "axios";

const BASE: string = "https://api.example.com";

export interface Invoice {
  id: string;
  amount: number;
}

export function listInvoices(): Promise<Invoice[]> {
  return axios.get(`${BASE}/invoices`, { params: { status: "open" } });
}

export function getInvoice(invoiceId: string): Promise<Invoice> {
  return axios.get(`${BASE}/invoices/${invoiceId}`);
}

export function createInvoice(amount: number): Promise<Invoice> {
  return axios.post(`${BASE}/invoices`, { amount: amount });
}

export function payInvoice(invoiceId: string, method: string): Promise<Invoice> {
  return axios.post(`${BASE}/invoices/${invoiceId}/pay`, { method: method });
}
