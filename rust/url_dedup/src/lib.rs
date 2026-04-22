use blake3::Hasher;
use pyo3::prelude::*;
use std::collections::HashSet;

#[pyclass]
struct UrlSet {
    hashes: HashSet<[u8; 16]>,
}

fn hash_url(url: &str) -> [u8; 16] {
    let mut hasher = Hasher::new();
    hasher.update(url.as_bytes());
    let mut out = [0u8; 16];
    out.copy_from_slice(&hasher.finalize().as_bytes()[..16]);
    out
}

#[pymethods]
impl UrlSet {
    #[new]
    fn new() -> Self {
        Self {
            hashes: HashSet::new(),
        }
    }

    fn add(&mut self, url: &str) {
        if !url.is_empty() {
            self.hashes.insert(hash_url(url));
        }
    }

    fn add_many(&mut self, urls: Vec<String>) -> usize {
        let before = self.hashes.len();
        for url in urls {
            if !url.is_empty() {
                self.hashes.insert(hash_url(&url));
            }
        }
        self.hashes.len().saturating_sub(before)
    }

    fn contains(&self, url: &str) -> bool {
        if url.is_empty() {
            return false;
        }
        self.hashes.contains(&hash_url(url))
    }

    fn __len__(&self) -> usize {
        self.hashes.len()
    }
}

#[pymodule]
fn rust_url_dedup(_py: Python, module: &PyModule) -> PyResult<()> {
    module.add_class::<UrlSet>()?;
    Ok(())
}
