// Fixture: an axios-based client for the miner test corpus.

import axios from "axios";

const BASE = "https://api.example.com";

export function listCustomers() {
  return axios.get(`${BASE}/customers`, { params: { limit: 20 } });
}

export function getCustomer(customerId) {
  return axios.get(`${BASE}/customers/${customerId}`);
}

export function createCustomer(email, name) {
  return axios.post(`${BASE}/customers`, { email: email, name: name });
}

export function updateCustomer(customerId, name) {
  return axios.put(`${BASE}/customers/${customerId}`, { name: name });
}

export function deleteCustomer(customerId) {
  return axios.delete(`${BASE}/customers/${customerId}`);
}

export function search(q) {
  return axios.request({
    url: "/search",
    method: "GET",
    params: { q: q },
  });
}
