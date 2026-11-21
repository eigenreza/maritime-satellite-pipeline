"""Models: gradient boosting baseline and a small convolutional network with two outputs.

CNN (convolutional neural network): learns patterns directly from the radar cut-out. Here it gets the
VV and VH cut-out (64 x 64 pixels at 10 m) plus the sea state, and predicts (1) the probability that the
object is a ship and (2) its length. Data augmentation: random 90 degree rotations and flips.
Calibration: temperature scaling on the calibration passes, so that "90% ship" means about 9 in 10.
"""

from __future__ import annotations

import numpy as np
import torch
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from torch import nn

# ------------------------------------------------------------------ baseline

def fit_baseline(x: np.ndarray, y: np.ndarray, seed: int):
    clf = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_leaf_nodes=15,
                                         l2_regularization=1.0, random_state=seed)
    clf.fit(x, y)
    return clf


def fit_baseline_length(x: np.ndarray, length: np.ndarray, seed: int):
    reg = HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, max_leaf_nodes=15, random_state=seed,
                                        loss="absolute_error")
    reg.fit(x, np.log(length))
    return reg


# ------------------------------------------------------------------ CNN

class ShipNet(nn.Module):
    """Image branch (VV, VH cut-out) plus the detector's own measurements and the sea state.

    The length head predicts log length in units of the training spread and is bounded to +-3 spreads,
    so a strange cut-out cannot produce an absurd length.
    """

    def __init__(self, n_scalar: int, width: int = 16, ll_mu: float = 4.4, ll_sd: float = 0.8):
        super().__init__()
        self.register_buffer("ll_mu", torch.tensor(float(ll_mu)))
        self.register_buffer("ll_sd", torch.tensor(float(ll_sd)))

        def block(i, o):
            return nn.Sequential(nn.Conv2d(i, o, 3, padding=1), nn.BatchNorm2d(o), nn.ReLU(),
                                 nn.Conv2d(o, o, 3, padding=1), nn.BatchNorm2d(o), nn.ReLU(), nn.MaxPool2d(2))

        self.features = nn.Sequential(block(2, width), block(width, 2 * width), block(2 * width, 4 * width),
                                      nn.AdaptiveAvgPool2d(1), nn.Flatten())
        self.head = nn.Sequential(nn.Linear(4 * width + n_scalar, 64), nn.ReLU(), nn.Dropout(0.3))
        self.cls = nn.Linear(64, 1)
        self.reg = nn.Linear(64, 1)

    def forward(self, img, scalars):
        h = self.head(torch.cat([self.features(img), scalars], dim=1))
        z = 3.0 * torch.tanh(self.reg(h).squeeze(1) / 3.0)
        return self.cls(h).squeeze(1), self.ll_mu + self.ll_sd * z


def augment(x: torch.Tensor) -> torch.Tensor:
    k = int(torch.randint(0, 4, (1,)))
    x = torch.rot90(x, k, dims=(2, 3))
    if torch.rand(1) < 0.5:
        x = torch.flip(x, dims=(3,))
    return x


def train_cnn(img, scal, y, log_len, has_len, img_val, scal_val, y_val, seed: int, epochs: int = 25,
              batch: int = 64, device: str = "cpu", log=print) -> ShipNet:
    torch.manual_seed(seed)
    np.random.seed(seed)
    ll_ships = log_len[has_len.astype(bool)]
    net = ShipNet(scal.shape[1], ll_mu=float(ll_ships.mean()), ll_sd=float(ll_ships.std() + 1e-3)).to(device)
    opt = torch.optim.AdamW(net.parameters(), lr=2e-3, weight_decay=1e-3)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=3e-3, total_steps=epochs * int(np.ceil(len(y) / batch)))
    pos_w = torch.tensor((1 - y.mean()) / max(y.mean(), 1e-3), dtype=torch.float32, device=device)
    bce = nn.BCEWithLogitsLoss(pos_weight=pos_w.clamp(0.2, 5.0))
    t = {k: torch.as_tensor(v) for k, v in dict(img=img, scal=scal, y=y.astype(np.float32),
                                               ll=log_len.astype(np.float32), hl=has_len.astype(np.float32)).items()}
    best, best_state = np.inf, None
    for ep in range(epochs):
        net.train()
        perm = torch.randperm(len(y))
        tot = 0.0
        for i in range(0, len(y), batch):
            b = perm[i:i + batch]
            xb = augment(t["img"][b].to(device))
            logit, pred_ll = net(xb, t["scal"][b].to(device))
            loss = bce(logit, t["y"][b].to(device))
            hl = t["hl"][b].to(device)
            if hl.sum() > 0:
                err = torch.abs(pred_ll - t["ll"][b].to(device)) / net.ll_sd
                loss = loss + 0.5 * (err * hl).sum() / hl.sum()
            opt.zero_grad()
            loss.backward()
            opt.step()
            sched.step()
            tot += float(loss) * len(b)
        vl = val_loss(net, img_val, scal_val, y_val, device)
        if vl < best:
            best, best_state = vl, {k: v.detach().clone() for k, v in net.state_dict().items()}
        log(f"epoch {ep + 1:2d} train loss {tot / len(y):.4f} validation log loss {vl:.4f}")
    net.load_state_dict(best_state)
    return net


@torch.no_grad()
def predict(net: ShipNet, img, scal, device: str = "cpu", batch: int = 512) -> tuple[np.ndarray, np.ndarray]:
    net.eval()
    logits, lls = [], []
    for i in range(0, len(img), batch):
        lo, ll = net(torch.as_tensor(img[i:i + batch]).to(device), torch.as_tensor(scal[i:i + batch]).to(device))
        logits.append(lo.cpu().numpy())
        lls.append(ll.cpu().numpy())
    return np.concatenate(logits), np.exp(np.concatenate(lls))


def val_loss(net, img, scal, y, device="cpu") -> float:
    logit, _ = predict(net, img, scal, device)
    p = 1 / (1 + np.exp(-logit))
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def fit_temperature(logits: np.ndarray, y: np.ndarray) -> float:
    """Temperature T minimizing log loss of sigmoid(logit / T) on the calibration passes."""
    best_t, best = 1.0, np.inf
    for t in np.exp(np.linspace(np.log(0.25), np.log(8.0), 200)):
        p = np.clip(1 / (1 + np.exp(-logits / t)), 1e-6, 1 - 1e-6)
        ll = -np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))
        if ll < best:
            best, best_t = ll, t
    return float(best_t)


def expected_calibration_error(p: np.ndarray, y: np.ndarray, bins: int = 10) -> tuple[float, list[dict]]:
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges) - 1, 0, bins - 1)
    ece, curve = 0.0, []
    for b in range(bins):
        m = idx == b
        if m.sum() == 0:
            continue
        conf, acc = float(p[m].mean()), float(y[m].mean())
        ece += m.mean() * abs(conf - acc)
        curve.append({"bin": b, "predicted": round(conf, 4), "observed": round(acc, 4), "n": int(m.sum())})
    return float(ece), curve
