// Fixture: a fetch-based client for the miner test corpus.

const BASE = "https://api.example.com";

export function listProducts() {
  return fetch(`${BASE}/products?limit=20`);
}

export function getProduct(productId) {
  return fetch(`${BASE}/products/${productId}`);
}

export function createProduct(name, price) {
  return fetch(`${BASE}/products`, {
    method: "POST",
    body: JSON.stringify({ name: name, price: price }),
  });
}

export function deleteProduct(productId) {
  return fetch(`${BASE}/products/${productId}`, { method: "DELETE" });
}

export function ping() {
  return fetch("/healthz");
}
