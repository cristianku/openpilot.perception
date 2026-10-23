# Choosing the object detector (research, 2026-10-23)

RECOMMENDATION: a pretrained 2D detector for openpilot.perception (MIT), as of 2026-10-23

I checked three things myself in this pass, on top of the findings you supplied:
- I queried the Hugging Face API for the revisions, licence tags and LFS SHA-256 of each checkpoint.
- I read the pinned config.json and preprocessor_config.json files.
- I checked transformers, trackers and supervision on PyPI.
- I ran a CPU-only test with CUDA hidden (a throwaway script, not kept). Setup: torch 2.13.0+cu126, transformers 5.20.2, 8 threads, fp32, one COCO image (000000039769) resized to 1344x760.

Everything else comes from the supplied verified findings. Where a fact is unverified, I say so. None of this is legal advice.

1. FIRST CHOICE: D-FINE-X, COCO-only checkpoint, loaded through Hugging Face transformers

Why this one
- Code and weights are both under a permissive licence (Apache-2.0).
- It is the only checkpoint family whose authors say plainly that the training data is COCO only. They mark their own Objects365 checkpoints (*_obj365, *_obj2coco) as "should not be assumed to be commercially cleared". The *-coco checkpoints are the clean ones.
- It is the strongest COCO-only, permissive model that ships inside transformers. No vendored code, no custom CUDA kernels, no second framework.
- It is the direct successor of the RT-DETR I suggested earlier, and 1.5 to 2.4 AP better at similar latency (RT-DETR-R101 54.3, RT-DETRv2-X 54.3). The latency cost does not matter here, because inference runs offline on the V100.
- It handles a non-square input. I confirmed this locally: config anchor_image_size=None, and pixel_values (1,3,768,1344) ran.
- Two COCO-only Apache models score higher: RT-DETRv4-X (57.0 AP / 39.5 AP_S) and DEIM-D-FINE-X (56.5 / 38.8). Both need vendored code. RT-DETRv4 also carries an open question about its DINOv3 teacher licence, and DEIM comes from Intellindust, which relicensed DEIMv2 and EdgeCrafter as non-commercial on 2026-08-24.

Licence
- Model code: Apache-2.0.
  - D-FINE repository: https://github.com/Peterande/D-FINE/blob/master/LICENSE
  - The implementation actually used is transformers, also Apache-2.0 (PyPI: "Apache 2.0 License"): https://github.com/huggingface/transformers/blob/main/LICENSE
- Weights: Apache-2.0 by Hugging Face tag. I checked through the HF API: tags license:apache-2.0 and dataset:coco. https://huggingface.co/ustc-community/dfine-xlarge-coco
- The authors' note on the Objects365 checkpoints is at https://github.com/Peterande/D-FINE#model-zoo
- Caveats on the training data:
  - The HGNetv2 backbone is pretrained on ImageNet. ImageNet's access terms are "non-commercial research and educational purposes" (https://image-net.org/accessagreement.php).
  - COCO images fall under Flickr terms.
  - Nearly every detector shares these two points. Whether dataset terms bind trained weights is legally unsettled.

Accuracy and speed
- COCO val2017, 640x640: 55.8 AP, 37.3 AP_S. Sources: README, and arXiv 2410.13842 Table 1 (https://arxiv.org/abs/2410.13842).
- Published speed: 12.89 ms on a T4 with TensorRT 10.4 FP16, batch 1, at 640.
- No V100 number is published by anyone.
- My CPU measurements (warm forward pass): 714 ms at 640x640, 1737 ms at 768x1344.

Installing and pinning
- Add transformers==5.20.2 to the pyproject.toml of this repository (as an extra or a main dependency), for example `detect = ["transformers==5.20.2"]`, then `uv lock`. Release 5.20.2 was uploaded on 2026-10-19 under Apache-2.0. DFineForObjectDetection exists since 4.52.0.
- In my test environment torchvision 0.28.0+cu126 and Pillow were also present. I did not check whether the image processor (RTDetrImageProcessor) strictly needs torchvision.
- Pin values:
  - Repository: ustc-community/dfine-xlarge-coco
  - Revision: ea4f6be7350bbe3c199ec6febc74168346cb5a68 (lastModified 2025-05-05)
  - model.safetensors: 251,619,400 bytes, SHA-256 3ac6ea37c6c995ed65a10beeda703249e97aac1bb40bda18cfaa1c2f6cdca4c5. The HF LFS metadata and my local hash of the downloaded file match.
  - The revision is a git commit, so it also pins config.json and preprocessor_config.json.
- Loading code:
    path = huggingface_hub.hf_hub_download(REPO, "model.safetensors", revision=REV, cache_dir="~/.cache/openpilot-perception")
    assert hashlib.sha256(open(path, "rb").read()).hexdigest() == SHA256
    proc = AutoImageProcessor.from_pretrained(REPO, revision=REV)
    model = DFineForObjectDetection.from_pretrained(REPO, revision=REV, use_safetensors=True).eval()
  From a shell, `sha256sum <file>` gives the same check.
- Before first use, add a row to THIRD_PARTY.md with the model, the code and weights licences, the datasets (COCO, plus the ImageNet backbone), the revision and the SHA-256. That file requires it.
- For CPU tests, use ustc-community/dfine-nano-coco:
  - Revision 066438d3d8f0da137a37b38fdf3368fd4afceced
  - model.safetensors: 15,278,996 bytes, SHA-256 19e06bdc873da819920a8d373b879721a5b9759d822f8213220bb09abbdab58b (checked locally)
  - 42.8 AP; 98 ms on CPU at 640.
- A cheaper GPU option is ustc-community/dfine-large-coco:
  - Revision aa8f8211545e4cff5adf52ea512f9ac74a9824ec
  - SHA-256 4e9d77ba937ab6eb8537b183c1c05372262daaa552e7682d721417042e24a8dc (HF metadata only; I did not hash it locally)
  - 54.0 AP, 8.07 ms on a T4.

Preprocessing
- Keep the shipped processor as it is: rescale by 1/255, do_normalize=false, resize to 640x640 without keeping the aspect ratio. Do not add ImageNet normalisation.
- Pass `size={"height": H, "width": W}` to choose the input size. Sides must be multiples of 32.

Precision
- Run fp32 first on the V100.
- The RF-DETR paper reports that "naively quantizing D-FINE to FP16 reduces performance to 0.5 AP" in a TensorRT export.
- PyTorch fp16 autocast on the V100 is untested. Switch to fp16 only after comparing boxes and scores against fp32 on a few hundred frames.
- TensorRT on Volta stops at 10.4.

Classes
- From the pinned config.json, 80 labels: 0 person, 1 bicycle, 2 car, 3 "motorbike", 5 bus, 6 train, 7 truck.
- Map by name, never by index:
  - car, truck, bus → vehicle
  - motorbike → motorcycle
  - bicycle and person kept as they are
- A cyclist comes out as two boxes, person and bicycle.
- COCO has no van, SUV, trailer or cone class. A van usually comes out as car or truck.

Resolution: this matters most for the 40 to 80 m range
- Focal length on the 1344x760 OS04C10 camera: 1141.5 px.
- A 1.8 m wide car at 80 m is about 26 px wide at native resolution and about 12 px after the 640x640 squash.
- My single-image CPU test showed that running the whole frame at a size the model was not trained at has a cost:
  - D-FINE-X at 768x1344 kept the main objects, but with lower scores and three extra false positives.
  - D-FINE-N lost three of its five detections.
  - RF-DETR-L at 768x1344 found only one of five objects.
- So use two passes, then merge them with class-wise NMS:
  - Pass A: the full frame at 640x640, as trained, for near objects.
  - Pass B: a native-resolution 640x640 crop centred on the vanishing point from the calibration. That covers about 31 degrees, or about ±22 m sideways at 80 m.
- This scheme is our own proposal and is unbenchmarked. Validate recall against the radar lead distance on your own drives.

2. ALTERNATIVE: RF-DETR-Large (Roboflow), in transformers or the native rfdetr package

When to use it
- If D-FINE does not hold up on the road frames, or its fp16 does not work, RF-DETR is a different architecture (a DINOv2 ViT backbone).
- It also has a second runtime path that does not go through transformers.

Licence
- Code: Apache-2.0.
  - https://github.com/roboflow/rf-detr/blob/develop/LICENSE
  - rfdetr on PyPI: "Apache License 2.0"
- Weights: Apache-2.0.
  - README licence section: https://github.com/roboflow/rf-detr#license
  - HF tag license:apache-2.0, checked: https://huggingface.co/Roboflow/rf-detr-large
- The DINOv2 backbone is Apache-2.0.
- Caveat: the paper's recipe (arXiv 2511.09554, Tables 5 and 6, Appendix A) pretrains on Objects365 pseudo-labelled with SAM2. Objects365 is "available for the academic purpose only" (https://www.objects365.org/download.html).
  - The HF card tags only dataset:coco and does not disclose this.
  - The paper does not state Objects365 pretraining per released size; it is inferred from the recipe.
  - This is the same issue D-FINE's authors flag for their own obj2coco weights.
- Never install rfdetr[plus], and never use the XL, 2XL, Atto, Femto or Pico sizes. They are under PML-1.0: not open source, usage tracking, a platform plan required.

Accuracy and speed
- COCO val2017 at 704: 56.5 AP, 39.0 AP_S.
- T4, TensorRT FP16, batch 1: 6.8 ms. No V100 number is published.
- My CPU measurement: 383 ms warm at 704x704.

Installing and pinning
- transformers==5.20.2 provides RfDetrForObjectDetection (it exists since 5.9.0).
  - Repository: Roboflow/rf-detr-large
  - Revision: f62f7dd5252b61097cbace33886045816dadbde9
  - model.safetensors: 135,795,376 bytes, SHA-256 1ec604598aea748627eb5755a9989f3c80839fc7fb977a91bfba107ee76fcd7d (checked locally)
  - Same download, checksum and from_pretrained pattern as in section 1.
- Without transformers: rfdetr==1.11.2 (Apache-2.0, uploaded 2026-10-04), no extras. It downloads the weights from storage.googleapis.com/rfdetr with an MD5 check.
  - Its predict(shape=(H, W)) accepts rectangles with sides divisible by 32.
  - Given my test result, stay at 704x704: the full-frame squash plus native 704x704 crops.
- Classes: 91-id COCO map with gaps (pinned config). 1 person, 2 bicycle, 3 car, 4 "motorcycle", 6 bus, 7 train, 8 truck. Map by name.

Last-resort fallback that adds no dependency
- torchvision FasterRCNN_ResNet50_FPN_V2: 46.7 AP, BSD-3 code.
- Its weights have no explicit licence, and the torchvision docs push the dataset-terms question to the user.

3. WHAT YOLO IS, AND WHY NOT ULTRALYTICS

What YOLO is
- "YOLO" (You Only Look Once) is a 2015 idea: a detector that predicts every box and class in a single pass of one network.
- Since then it has become a brand name used by many unrelated groups. Each version has its own licence, set by whoever wrote it.

Ultralytics (YOLOv5, YOLOv8, YOLO11, YOLO26)
- All AGPL-3.0, code and weights.
- Ultralytics' licence page: "All Ultralytics YOLO trained models fall under the AGPL-3.0 License by default", and that includes deployment through "a SaaS platform, API, or other private system". https://www.ultralytics.com/license and https://pypi.org/project/ultralytics/
- The same applies to the forks built on the ultralytics code: YOLOv10, YOLO12, YOLOv13, YOLOE.
- It also applies to RT-DETR loaded through `from ultralytics import RTDETR`.

Is AGPL acceptable here?
AGPL is an open-source licence, so publishing the code is not the problem. Using it would have these consequences:
- openpilot.perception, MIT today, would have to be distributed as an AGPL combined work. Ultralytics' own position goes further: the "entire project".
- The web site that serves it falls under the AGPL network clause (section 13). Its complete source would have to be offered to its users.
- openpilot and sunnypilot (MIT) could not merge it unless they relicensed the combined work under AGPL.
- The paid Enterprise licence protects only the buyer. Its terms say you may not "Sublicense, resell, or otherwise transfer the license", so it cannot make the repo MIT for anyone downstream.

There is also no accuracy reason to accept AGPL:
- YOLO26-X: 57.5 AP / 41.8 AP_S, 11.8 ms on a T4, pretrained on Objects365.
- Permissive alternatives: RF-DETR-L 56.5 / 39.0, D-FINE-X COCO-only 55.8 / 37.3.

Verdict: not acceptable for this repository's goal.

Other licence traps
- GPL-3.0: YOLOv6 (Meituan), the original YOLOv7 and YOLOv9 repositories, Gold-YOLO, MMYOLO, YOLO-World. Same merge problem, without the network clause.
- YOLO-NAS weights: proprietary, no redistribution, non-commercial.
- DEIMv2 and EdgeCrafter: non-commercial since 2026-08-24.

Permissive YOLO-style models

| Model | Licence | COCO AP | Notes |
| --- | --- | --- | --- |
| YOLOX | Apache-2.0 | 51.1 (x) | Only family with a published V100 time: 17.3 ms. Old. https://github.com/Megvii-BaseDetection/YOLOX |
| YOLOv9/v7 re-release by the original author (MultimediaTechLab/YOLO) | MIT (code and v1.0-alpha weights) | below the paper (users report v9-c about 51.6 vs 53.0) | "Work In Progress, not recommended for production". The two research passes disagree on whether the weights' provenance is settled. https://github.com/MultimediaTechLab/YOLO |
| RTMDet in MMDetection, not MMYOLO | Apache-2.0 | 52.8 (x), 54.9 (x-P6 at 1280) | mmcv must be built from source for torch 2.13 |
| DAMO-YOLO | Apache-2.0 | — | Abandoned |
| PP-YOLOE | Apache-2.0 | — | Needs Paddle; the "+" version uses Objects365 |

- LibreYOLO is an MIT wrapper, but its per-weight licences vary, including cc-by-nc. Allow-list specific checkpoints if you use it.
- All of these trail D-FINE and RF-DETR.

4. TRACKER

- Use roboflow trackers==2.6.1: Apache-2.0, PyPI classifier checked, uploaded 2026-09-25. https://github.com/roboflow/trackers
- Use ByteTrack, or BoT-SORT with camera-motion compensation (pass the frame to update()). No ReID weights are needed.
- MOT17 HOTA from the README, verified by the research pass and not by me: ByteTrack 60.1, OC-SORT 61.9, BoT-SORT 63.7.
- Install the base package only:
  - The "detection" extra pulls Roboflow's inference-models, whose licence I did not check.
  - The "mask" extra pulls SAM-derived packages.
- Base dependencies: supervision>=0.26.1 (current 0.31.1, MIT), scipy, opencv-python, rich, requests, pydeprecate.
  - supervision itself pulls in av (PyAV) and matplotlib.
  - I did not check the licence of the FFmpeg libraries bundled in the PyAV and OpenCV wheels.
- Fallback: vendor one of the MIT reference repositories (ifzhang/ByteTrack, NirAharon/BoT-SORT, noahcao/OC_SORT).
- Avoid:
  - BoxMOT (AGPL-3.0)
  - abewley/sort, nwojke/deep_sort and StrongSORT (GPL-3.0)
  - the Ultralytics trackers (AGPL)

5. OPEN QUESTIONS AND WHAT I COULD NOT VERIFY

- V100 behaviour has not been measured for any DETR model, in fp32 or fp16. I did not start a GPU job.
- Road accuracy is unknown:
  - My only test was on a single non-road COCO image, on CPU.
  - Both the two-pass crop scheme and the claim that native resolution helps at 40 to 80 m are unvalidated. Measure recall and box distance against the radar on your own drives.
- I did not check whether the ustc-community HF organisation is the D-FINE authors' own upload, and nobody has re-measured the AP of the HF port.
- RF-DETR's Objects365 exposure is inferred from the paper; it is not stated per released checkpoint.
- Dataset terms (ImageNet backbone, COCO under Flickr terms, Objects365): it is unsettled whether they reach the trained weights.
- Whether transformers' RTDetrImageProcessor needs torchvision.
- Still unverified from the earlier findings:
  - YOLOX's V100 measurement protocol
  - the weights' provenance in MultimediaTechLab/YOLO
  - whether mmcv builds against torch 2.13
  - whether DEIM checkpoints load into DFineForObjectDetection
  - how RT-DETRv4's DINOv3 "derivative works" clause applies to a distilled model
  - the FFmpeg licence inside the PyAV and OpenCV wheels
- I edited nothing in the repository. The THIRD_PARTY.md and pyproject.toml changes in section 1 are still to do.
