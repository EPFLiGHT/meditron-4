# Distillation Output Examples

One representative distilled sample per dataset (exactly one example each).

| dataset | source_rows | distilled_rows | coverage | empty_answer_rate | notes |
|---|---:|---:|---:|---:|---|
| medqa_with_labels_5opt.jsonl | 10178 | 10176 | 99.98% | 0.82% | deduped |
| medmcqa_with_labels.jsonl | 182822 | 182789 | 99.98% | 0.37% | deduped |
| pubmedqa_pqa_labeled_1000_medgemma_style_with_labels.jsonl | 1000 | 1000 | 100.00% | 0.00% | - |
| medexpqa_train_434.jsonl | 434 | 424 | 97.70% | 0.47% | deduped |
| afrimedqa_v1_test_3000_with_labels.jsonl | 3000 | 3000 | 100.00% | 0.03% | - |
| afrimedqa_v2_full_15275_with_labels.jsonl | 15275 | 15275 | 100.00% | 0.07% | - |
| healthsearchqa_3375_no_labels.jsonl | 3375 | 3375 | 100.00% | 5.99% | no ground-truth labels |
| liveqa_truehealth_635_with_labels.jsonl | 635 | 544 | 85.67% | 0.00% | deduped |

## Examples

### medqa_with_labels_5opt.jsonl

- id: `1`
- true_label: `E`
- question_preview: Question: A 23-year-old pregnant woman at 22 weeks gestation presents with burning upon urination. She states it started 1 day ago and has been worsening despite drinking more w...
- distilled_model: `openai/gpt-oss-120b`
- distilled_timestamp_utc: `2026-02-26T22:48:22.600957+00:00`

#### Source answer(s)

- true_answer_text: `Nitrofurantoin`
- true_label: `E`
- source_answer_idx: `E`
- source_assistant_answer:

> To determine the best treatment for this pregnant woman with symptoms suggestive of a urinary tract infection (UTI), we'll need to consider the safety and efficacy of potential antibiotics during pregnancy. Here is the step-by-step rationale:
>
> 1. **Evaluate the Symptoms and Diagnosis**:
>    - The patient has burning upon urination, which began 1 day ago and has worsened.
>    - She has no fever or other signs of systemic illness.
>    - Absence of costovertebral angle tenderness points away from pyelonephritis and supports a diagnosis of cystitis (bladder infection).
>
> 2. **Consider Patient’s Demographic and Pregnant State**:
>    - The patient is pregnant (22 weeks gestation).
>    - Safety of antibiotic use in pregnancy is crucial to avoid harm to both the mother and fetus.
>
> 3. **Review Antibiotic Options and Pregnancy Safety**:
>    - **Ampicillin**: Generally safe in pregnancy but less commonly used for UTIs due to resistance.
>    - **Ceftriaxone**: Used for more severe infections like pyelonephritis or other systemic infections and is typically not first-line for uncomplicated cystitis.
>    - **Ciprofloxacin**: A fluoroquinolone, contraindicated in pregnancy due to potential harm to fetal cartilage development.
>    - **Doxycycline**: A tetracycline, contraindicated in pregnancy as it can affect fetal bone growth and tooth discoloration.
>    - **Nitrofurantoin**: Broadly used for uncomplicated UTIs and generally considered safe in most stages of pregnancy, except near term due to the risk of neonatal hemolysis.
>
> 4. **Best Treatment Selection**:
>    - **Ampicillin**: Not preferred due to resistance issues.
>    - **Ceftriaxone**: More suited for severe cases and not first-line for simple cystitis.
>    - **Ciprofloxacin and Doxycycline**: Both are contraindicated in pregnancy.
>    - **Nitrofurantoin**: Effective for uncomplicated cystitis and safe in pregnancy (particularly up to the last weeks before term).
>
> Considering all the above points, **E. Nitrofurantoin** is the best treatment option for this patient's uncomplicated cystitis, balancing effectiveness and safety during pregnancy.
> **Answer**: E. Nitrofurantoin


#### Distilled answer

**Answer: E. Nitrofurantoin**

**Explanation**

- The patient’s presentation (dysuria without fever, flank pain, or systemic signs) is consistent with an uncomplicated lower urinary tract infection (cystitis).
- In pregnancy, uncomplicated cystitis should be treated promptly to prevent progression to pyelonephritis, which carries significant maternal and fetal risks.
- **First‑line oral agents for pregnant patients** (per ACOG and IDSA guidelines) are:
  - Nitrofurantoin (5 days) – safe throughout most of pregnancy, avoiding the last few weeks near term.
  - Fosfomycin (single dose) – also safe but not listed among the answer choices.
  - β‑lactams such as ampicillin/amoxicillin if the organism is known to be susceptible, but resistance rates are high and ampicillin alone is not the preferred empiric choice.
- **Contraindicated or less appropriate options** in pregnancy:
  - **Ciprofloxacin** (fluoroquinolone) – associated with fetal cartilage toxicity.
  - **Doxycycline** (tetracycline) – can cause teeth discoloration and inhibition of bone growth.
  - **Ceftriaxone** – a parenteral third‑generation cephalosporin; unnecessary for uncomplicated cystitis and not first‑line oral therapy.
- Nitrofurantoin provides excellent urinary tract coverage (E. coli, Enterococcus, Staphylococcus saprophyticus) and has a well‑established safety profile in pregnancy when used before 36 weeks gestation.

Therefore, the best treatment for this pregnant woman with uncomplicated cystitis is **nitrofurantoin**.

### medmcqa_with_labels.jsonl

- id: `301b5d37-ac36-4429-98aa-16a6ca7f0669`
- true_label: `C`
- question_preview: Schistosomiasis is transmitted by ?
A. Cyclops
B. Fish
C. Snaile
D. Cattle
- distilled_model: `openai/gpt-oss-120b`
- distilled_timestamp_utc: `2026-02-26T22:52:29.660630+00:00`

#### Source answer(s)

- true_label: `C`
- true_label_idx: `2`
- source_assistant_answer:

> **Transmission of Schistosomiasis**
>
> Schistosomiasis, also known as bilharzia, is a parasitic disease caused by Schistosoma species. The transmission of this disease involves a complex life cycle involving both a definitive host and an intermediate host.
>
> ### Life Cycle of Schistosomiasis
>
> - **Definitive Host:** Humans are the definitive hosts for Schistosoma species.
> - **Intermediate Host:** The intermediate host for Schistosoma species is a type of snail. These snails are responsible for harboring the larval stages of the parasite.
>
> ### How Transmission Occurs
>
> - **Infection Begins:** The life cycle of Schistosomiasis begins when an infected snail releases cercariae, the larval stages of the parasite, into the water.
> - **Human Infection:** Humans become infected when they come into contact with contaminated water while swimming or wading. The cercariae penetrate the skin, and the larvae mature into adult worms in the human host.
>
> ### Conclusion
>
> The intermediate host for Schistosoma species is a snail, which plays a crucial role in the transmission of the disease.
>
> ### Answer:
> C. Snail


#### Distilled answer

**Answer: C. Snail**

Schistosomiasis (bilharzia) is a parasitic disease caused by *Schistosoma* species. The parasites require freshwater **snails** as intermediate hosts. The larvae (cercariae) are released from infected snails into water, where they can penetrate human skin during contact with contaminated water. None of the other listed organisms (Cyclops, fish, cattle) serve as the intermediate host for *Schistosoma* spp.

### pubmedqa_pqa_labeled_1000_medgemma_style_with_labels.jsonl

- id: `21645374`
- true_label: `yes`
- question_preview: Context: Programmed cell death (PCD) is the regulated death of cells within an organism. The lace plant (Aponogeton madagascariensis) produces perforations in its leaves through...
- distilled_model: `openai/gpt-oss-120b`
- distilled_timestamp_utc: `2026-02-27T00:39:26.788189+00:00`

#### Source answer(s)

- true_label: `yes`
- true_label_abc: `A`
- source_assistant_answer: `Results depicted mitochondrial dynamics in vivo as PCD progresses within the lace plant, and highlight the correlation of this organelle with other organelles during developmental PCD. To the best of our knowledge, this is the first report of mitochondria and chloroplasts moving on transvacuolar strands to form a ring structure surrounding the nucleus during developmental PCD. Also, for the first time, we have shown the feasibility for the use of CsA in a whole plant system. Overall, our findings implicate the mitochondria as playing a critical and early role in developmentally regulated PCD in the lace plant.`

#### Distilled answer

**Yes – mitochondria are actively involved in the remodelling of lace‑plant leaves that occurs during developmentally regulated programmed cell death (PCD).**  

Below is a concise synthesis of the evidence presented in the paper, followed by an interpretation of what those data mean for the role of mitochondria in this unique leaf‑perforation process.

---

## 1.  What the authors observed

| Aspect examined | Findings in the three PCD zones (NPCD → EPCD → LPCD) |
|-----------------|--------------------------------------------------------|
| **Mitochondrial morphology & distribution** | • **M1 (NPCD)** – mitochondria are elongated, evenly distributed throughout the cytoplasm, and show robust motility. <br>• **M2 (early PCD)** – mitochondria become more fragmented, start to cluster near the nucleus and the periphery of the cell, and their movement slows. <br>• **M3 (late PCD)** – mitochondria are highly punctate, often aggregated in perinuclear “clouds,” and display little or no motility. <br>• **M4 (terminal PCD)** – mitochondria are severely depolarised, swollen, and often disappear from the fluorescence signal, indicating loss of membrane potential (ΔΨm). |
| **Mitochondrial membrane potential (ΔΨm)** | The intensity of MitoTracker Red (which requires an intact ΔΨm) declines progressively from M1 → M4, indicating a loss of mitochondrial electro‑chemical gradient as PCD advances. |
| **Nuclear DNA fragmentation (TUNEL assay)** | A gradient of TUNEL‑positive nuclei mirrors the mitochondrial stages: cells in M2–M3 show early DNA breaks, while cells in M4 exhibit extensive fragmentation. |
| **Other organelles** | Chloroplasts and trans‑vacuolar strands remain relatively intact until the very late stages, suggesting that mitochondrial changes precede or drive the downstream dismantling of other cellular components. |
| **Pharmacological manipulation (CsA)** | Cyclosporine A, an inhibitor of the mitochondrial permeability transition pore (mPTP), was applied in vivo. Treated leaves: <br>• Formed **significantly fewer perforations** (i.e., fewer cells completed PCD). <br>• Showed mitochondrial dynamics that resembled the **M1 (non‑PCD) phenotype** even in regions that would normally be undergoing PCD. <br>• Retained higher ΔΨm and mitochondrial motility, implying that blocking mPTP prevents the cascade of mitochondrial changes that normally lead to cell death. |

---

## 2.  How these observations point to a functional role for mitochondria

1. **Temporal correlation** – The stepwise shift from a healthy, motile mitochondrial network (M1) to a fragmented, depolarised, and immobile population (M4) occurs **in the same spatial gradient** where PCD initiates and spreads. The fact that mitochondrial changes **precede** the most obvious morphological signs of cell death (e.g., loss of cytoplasmic volume, leaf perforation) argues that they are not merely a by‑product but an upstream event.

2. **Mitochondrial membrane potential loss** – ΔΨm is essential for ATP synthesis, Ca²⁺ handling, and ROS production. Its progressive loss suggests that mitochondria are **failing as bioenergetic hubs**, which can trigger downstream death signals (e.g., ROS bursts, release of pro‑death factors).

3. **Link to nuclear DNA fragmentation** – The TUNEL gradient aligns with mitochondrial stages. In animal cells, mitochondrial outer‑membrane permeabilisation (MOMP) releases nucleases (e.g., Endonuclease G, AIF) that fragment nuclear DNA. The parallel in lace‑plant cells hints at a **conserved mechanistic link**: mitochondrial dysfunction → activation of nucleases → DNA fragmentation.

4. **Effect of cyclosporine A** – CsA blocks the **mitochondrial permeability transition pore (mPTP)**, a high‑conductance channel whose opening collapses ΔΨm, swells mitochondria, and can lead to cell death. The fact that CsA **preserves mitochondrial dynamics** (M1‑like) and **dramatically reduces perforation formation** demonstrates that **mPTP opening is a necessary step** for the PCD program in this plant.

5. **Specificity to P

### medexpqa_train_434.jsonl

- id: `None`
- true_label: `None`
- question_preview: Juan, a second year resident, attends Sofia, a 15 year old girl in the emergency room, who apparently fainted at school without losing consciousness. The patient says that she w...
- distilled_model: `openai/gpt-oss-120b`
- distilled_timestamp_utc: `2026-02-27T00:39:57.389060+00:00`

#### Source answer(s)

- source_assistant_answer: `There is no doubt that the doctor-patient relationship today transcends the physical environment of the consultation room. But we must not forget that we must maintain the same ethical and professional codes as in the real environment. The code of ethics Article 26-3. The clinical practice of medicine by means of consultations exclusively by letter, telephone, radio, press or internet, is contrary to the deontological norms. [HIDDEN] the personal and direct contact between the doctor and the patient.`

#### Distilled answer

**Short answer:**  
Juan should **politely decline** Sofia’s Facebook‑friend request and explain that, as a physician, he must keep his relationship with patients strictly professional and limited to the clinical setting (or to secure, health‑care‑specific communication tools). He can reassure her that he is still available to answer any questions she has while she is in the emergency department, but that personal social‑media connections with patients—especially minors—are not appropriate.

---

## Why this is the best response

| Ethical/Professional Principle | How it applies to the situation |
|--------------------------------|---------------------------------|
| **Professional boundaries**   | Physicians are expected to keep a clear line between their personal life and their clinical role. Accepting a friend request blurs that line and can create a dual relationship (doctor‑patient + personal friend). |
| **Confidentiality & privacy** | Social‑media platforms are not secure for protected health information. Even a “friend” status could expose the patient (or the physician) to inadvertent disclosure of health‑related details. |
| **Power differential**         | The physician holds a position of authority; a minor patient may feel pressured to accept or to comply with a request that she perceives as “expected.” Maintaining a professional distance protects the patient from feeling coerced. |
| **Legal/Institutional policy**| Most hospitals, residency programs, and medical boards have explicit policies prohibiting personal social‑media contact with current patients (and especially with minors). Violating those policies can lead to disciplinary action. |
| **Beneficence & non‑maleficence**| The goal is to help the patient without causing harm. A social‑media connection could lead to misunderstand‑​related expectations, boundary creep, or even legal liability, which would not be in Sofia’s best interest. |
| **Patient‑centered communication**| Declining the request does **not** mean being cold or dismissive. It can be done in a warm, empathetic way that reinforces the therapeutic alliance while preserving professional limits. |

---

## How Juan can phrase his response

> “I’m glad you feel comfortable talking with me, Sofia, and I’m happy to answer any questions you have while you’re here. However, as a doctor I’m not allowed to add patients to my personal social‑media accounts. It’s important for me to keep my professional and personal lives separate so I can give you the best care and protect your privacy. If you need anything while you’re in the emergency department, you can let me know here, or we can arrange a follow‑up through the hospital’s patient portal or by calling the clinic. Does that sound okay?”

Key elements of this reply:

1. **Acknowledge the request positively** – shows respect for the patient’s desire to connect.
2. **State the boundary clearly** – “I’m not allowed to add patients on personal social media.”
3. **Explain the reason** – professional standards, privacy, and safety.
4. **Offer an alternative** – reassure that she can still communicate with him through appropriate, secure channels.
5. **Invite her to continue the conversation** – keeps the therapeutic relationship open.

---

## What NOT to do

| Action | Why it’s inappropriate |
|--------|------------------------|
| Accept the friend request | Violates professional boundaries, institutional policy, and may expose both parties to privacy breaches. |
| Ignore the request without comment | Leaves the patient confused and may be perceived as dismissive or uncaring. |
| Offer to “chat” on personal messaging apps (e.g., WhatsApp, Instagram) | Same boundary issues; these platforms are not HIPAA‑compliant and are not meant for clinical communication. |
| Make a joke or downplay the request (“Sure, I’ll add you, we’re friends now!”) | Undermines the seriousness of boundary maintenance and can erode trust

### afrimedqa_v1_test_3000_with_labels.jsonl

- id: `265049d633cd0f3229d266a5284aa104eba52196034cb16afe7a0a70bac253f6`
- true_label: `option4`
- question_preview: Question: A 34-year-old man presents with a 1-week history of muscle aches, fatigue, and fever that occurs every 2 days. His history is significant for a trip to Nepal. Vital si...
- distilled_model: `openai/gpt-oss-120b`
- distilled_timestamp_utc: `2026-02-27T00:42:20.217478+00:00`

#### Source answer(s)

- true_label: `option4`
- true_rationale:

> A patient with recent travel history, cyclical fever, and brick red granules (Schüffner stippling) in erythrocytes suggests the diagnosis of malaria. The patient was most likely treated with chloroquine, a quinoline derivative that is a first-line agent for Plasmodium vivax and P. ovale infections. Chloroquine can cause marked QT prolongation and can cause severe ventricular arrhythmias like torsades de pointes.
>
> Patients on antimalarials should be monitored with an ECG regularly. The common side effect of chloroquine is gastrointestinal upset (GI), headache, blurred vision, and insomnia.
>
> Doxycycline and tetracycline are usually well-tolerated, but some GI upset can be seen and, less commonly, ultraviolet photosensitivity
>
> Metronidazole and pyrimethamine drugs are not effective against malaria.

- source_assistant_answer:

> Correct answer: option4
> Explanation: A patient with recent travel history, cyclical fever, and brick red granules (Schüffner stippling) in erythrocytes suggests the diagnosis of malaria. The patient was most likely treated with chloroquine, a quinoline derivative that is a first-line agent for Plasmodium vivax and P. ovale infections. Chloroquine can cause marked QT prolongation and can cause severe ventricular arrhythmias like torsades de pointes.
>
> Patients on antimalarials should be monitored with an ECG regularly. The common side effect of chloroquine is gastrointestinal upset (GI), headache, blurred vision, and insomnia.
>
> Doxycycline and tetracycline are usually well-tolerated, but some GI upset can be seen and, less commonly, ultraviolet photosensitivity
>
> Metronidazole and pyrimethamine drugs are not effective against malaria.


#### Distilled answer

**Answer: Chloroquine**  

The patient’s presentation (tertian fever, travel to Nepal, icterus, splenomegaly, and Schüffner stippling on the peripheral smear) is classic for *Plasmodium vivax* (or *P. ovale*) malaria. The standard treatment for chloroquine‑sensitive *P. vivax* infection is chloroquine (often followed by primaquine to eradicate hypnozoites).

One of the known acute cardiac toxicities of chloroquine is QT‑interval prolongation, which can precipitate a rapid, potentially life‑threatening ventricular tachyarrhythmia (e.g., torsades de pointes). The sudden increase in heart rate on hospital day 3 together with the ECG changes described is most consistent with this adverse effect of chloroquine.

### afrimedqa_v2_full_15275_with_labels.jsonl

- id: `ff784abc6b461a6a15d7f8bf5e6560d3393474fe4748699ff4c7f2124292b95f`
- true_label: `None`
- question_preview: Prompt: Your female friend complains of weight gain, high blood pressure and thinks she has Lupus Nephritis and is going to visit the nearest doctor. Question:
Question: Can you...
- distilled_model: `openai/gpt-oss-120b`
- distilled_timestamp_utc: `2026-02-27T00:47:56.832946+00:00`

#### Source answer(s)

_(no source answer field found for this sample)_

#### Distilled answer

I’m sorry you’re dealing with these concerns—it can feel overwhelming when you’re facing several health issues at once. While I’m not a medical professional, I can share some evidence‑based strategies that many people find helpful for reducing stress and supporting mental well‑being while they work with their health‑care team on conditions such as weight changes, high blood pressure, and lupus‑related kidney involvement.  

---

## 1. Build a Strong Support Network  

| What to Do | Why It Helps | How to Start |
|------------|--------------|--------------|
| **Talk to a trusted friend or family member** | Sharing worries reduces the feeling of “carrying it alone.” | Schedule a regular check‑in (e.g., a weekly phone call). |
| **Join a patient or caregiver group** (online or in‑person) | Hearing others’ stories normalizes your experience and provides practical tips. | Look for lupus, kidney disease, or hypertension groups on platforms like Facebook, Inspire, or local hospital support services. |
| **Consider a mental‑health professional** (psychologist, therapist, counselor) | Structured therapy (CBT, ACT, mindfulness‑based stress reduction) can lower anxiety and improve coping. | Ask your primary‑care doctor for a referral or use tele‑therapy services (e.g., BetterHelp, Talkspace). |

---

## 2. Daily Stress‑Reduction Practices  

| Technique | How to Do It (5‑10 min to start) | Frequency |
|-----------|----------------------------------|-----------|
| **Deep diaphragmatic breathing** | Inhale slowly through the nose for a count of 4, hold 2, exhale through the mouth for a count of 6. | 2–3 times daily, especially before stressful events. |
| **Progressive muscle relaxation** | Tense each muscle group for 5 s, then release, moving from toes to head. | Once a day, ideally before bedtime. |
| **Guided mindfulness meditation** | Use free apps (Insight Timer, Smiling Mind) or YouTube videos focused on “stress relief” or “body scan.” | 5–15 min daily; increase as comfortable. |
| **Grounding exercises** | Identify 5 things you can see, 4 you can touch, 3 you can hear, 2 you can smell, 1 you can taste. | When anxiety spikes. |
| **Short nature breaks** | Step outside, notice the sky, trees, or even a plant on a windowsill. | 5 min every few hours if possible. |

---

## 3. Lifestyle Tweaks That Support Both Physical and Mental Health  

1. **Gentle Physical Activity**  
   - **Why:** Exercise releases endorphins, improves blood pressure, and can help manage weight.  
   - **How:** Start with low‑impact options—walking, seated tai chi, water aerobics, or gentle yoga. Aim for **10‑15 minutes** most days, gradually building to 30 minutes if tolerated.  
   - **Safety tip:** Discuss any new exercise plan with your doctor, especially if kidney involvement or joint pain is present.

2. **Balanced Nutrition**  
   - **Focus on:**  
     - **Whole foods** (vegetables, fruits, whole grains, lean protein).  
     - **Reduced sodium** (≤ 1,500 mg/day) to help blood pressure.  
     - **Adequate potassium** (if kidney function allows) – bananas, sweet potatoes, beans.  
   - **Mindful eating:** Eat slowly, savor each bite, and notice hunger/fullness cues. This can lower stress‑related overeating.  
   - **Professional help:** A registered dietitian experienced with autoimmune or renal conditions can tailor a plan.

3. **Sleep Hygiene**  
   - **Goal:** 7–9 hours of restorative sleep.  
   - **Tips:** Keep a consistent bedtime, dim lights 30 min before sleep, limit caffeine after 2 pm, and create a calming pre‑sleep routine (e.g., reading, warm shower, gentle stretching).  

4. **Limit Stimulants

### healthsearchqa_3375_no_labels.jsonl

- id: `1`
- true_label: `None`
- question_preview: Are benign brain tumors serious?
- distilled_model: `openai/gpt-oss-120b`
- distilled_timestamp_utc: `2026-02-27T00:58:32.575253+00:00`

#### Source answer(s)

_(no source answer field found for this sample)_

#### Distilled answer

**Short answer:**  
Yes—​even though a benign brain tumor is not cancerous, it can still be a serious medical problem. Its impact depends mainly on **where it is, how big it gets, and how fast it grows**.

---

## Why “benign” doesn’t automatically mean “harmless”

| Feature | What it means for brain tumors |
|---------|--------------------------------|
| **Non‑cancerous (benign)** | The tumor cells do not invade surrounding brain tissue or spread (metastasize) to other parts of the body. |
| **Growth pattern** | Benign tumors usually grow more slowly than malignant ones, but they can still enlarge over weeks to years. |
| **Encapsulation** | Many are surrounded by a capsule, which can make surgical removal easier, but the capsule itself can press on nearby structures. |
| **Location matters** | The brain is a confined space. Even a small mass can compress critical areas (e.g., brainstem, optic nerves, motor cortex) and cause severe symptoms. |
| **Potential for complications** | • Increased intracranial pressure (headache, nausea, vomiting, vision changes)  <br>• Seizures  <br>• Neurological deficits (weakness, speech problems, vision loss)  <br>• Hydrocephalus (fluid buildup)  <br>• Hormonal disturbances if the tumor is near the pituitary gland |

---

## Common types of benign brain tumors and typical concerns

| Tumor type | Typical location | Typical symptoms | Typical prognosis (with treatment) |
|------------|------------------|------------------|------------------------------------|
| **Meningioma** | Meninges (coverings of the brain) – often near the convexities, skull base, or falx | Headache, seizures, focal neurological deficits, visual changes | Often curable with surgery; many patients live normal lives. |
| **Pituitary adenoma** | Pituitary gland (sella turcica) | Hormonal imbalances (e.g., prolactin excess, Cushing’s), vision loss (bitemporal hemianopsia) | Medical therapy (e.g., dopamine agonists) or surgery can control most cases. |
| **Acoustic neuroma (vestibular schwannoma)** | Vestibular nerve (inner ear) | Hearing loss, tinnitus, balance problems, facial nerve weakness | Slow‑growing; observation, radiosurgery, or microsurgical removal are options. |
| **Hemangioblastoma** | Cerebellum, brainstem, spinal cord | Headache, ataxia, nausea, vomiting | Surgical removal usually curative; may be associated with von Hippel‑Lindau disease. |
| **Craniopharyngioma** | Near the pituitary/hypothalamus | Vision loss, hormonal deficits, obesity, growth problems (in children) | Requires surgery ± radiation; long‑term endocrine follow‑up often needed. |

---

## How seriousness is assessed

1. **Size & growth rate** – Larger or rapidly enlarging tumors increase pressure on brain tissue.
2. **Location** – Tumors near vital centers (brainstem, optic chiasm, motor cortex) cause more severe deficits even when small.
3. **Symptoms** – New or worsening headaches, seizures, visual changes, weakness, speech problems, or hormonal disturbances signal that the tumor is affecting function.
4. **Edema & hydrocephalus** – Tumors can cause surrounding swelling (edema) or block cerebrospinal fluid flow, both of which raise intracranial pressure and can be life‑threatening if untreated.
5. **Patient factors** – Age, overall health, and comorbidities influence treatment risk and prognosis.

---

## Typical management pathway

| Step | What it involves | Goal |
|------|------------------|------|
| **1. Diagnosis** | MRI (often with contrast) ± CT; sometimes MR spectroscopy or perfusion studies. | Confirm tumor type, size, location, and effect on surrounding tissue. |
| **2. Observation (“watchful waiting”)** | Serial imaging every 6–12 months for small, asymptomatic tumors. | Avoid unnecessary surgery; intervene if growth or symptoms appear. |
| **3. Medical therapy** | • Hormone‑blocking drugs for prolactinomas (e.g., cabergoline).<br>• Steroids to reduce edema.<br>• Anticonvulsants for seizures. | Control tumor‑related hormonal or symptomatic issues. |

### liveqa_truehealth_635_with_labels.jsonl

- id: `1`
- true_label: `Cardiac amyloidosis is a disorder caused by deposits of an abnormal protein (amyloid) in the heart tissue. These deposits make it hard for the heart to work properly.`
- question_preview: Literature on Cardiac amyloidosis.  Please let me know where I can get literature on Cardiac amyloidosis.  My uncle died yesterday from this disorder.  Since this is such a rare...
- distilled_model: `openai/gpt-oss-120b`
- distilled_timestamp_utc: `2026-02-27T00:59:28.199770+00:00`

#### Source answer(s)

- true_label: `Cardiac amyloidosis is a disorder caused by deposits of an abnormal protein (amyloid) in the heart tissue. These deposits make it hard for the heart to work properly.`
- source_assistant_answer: `Cardiac amyloidosis is a disorder caused by deposits of an abnormal protein (amyloid) in the heart tissue. These deposits make it hard for the heart to work properly.`

#### Distilled answer

I’m very sorry for your loss. Below is a curated list of sources where you can obtain high‑quality, freely available literature on cardiac amyloidosis that you can print or share at the service. Because you are a retired NIH employee you may already have access to many of these resources on campus, but all of the links work for anyone with internet access.

---

## 1. NIH‑Hosted Resources (Free PDFs)

| Source | What you’ll find | How to obtain |
|--------|------------------|---------------|
| **National Heart, Lung, and Blood Institute (NHLBI) – “Cardiac Amyloidosis” fact sheet** | A 2‑page patient‑friendly overview (definition, symptoms, diagnosis, treatment options). | Download directly: <https://www.nhlbi.nih.gov/health-topics/cardiac-amyloidosis> → “Download PDF”. |
| **NIH National Institute on Aging (NIA) – “Amyloidosis: A Guide for Patients and Families”** | Broad overview of systemic amyloidosis with a dedicated section on cardiac involvement. | PDF: <https://www.nia.nih.gov/health/amyloidosis> → “Patient handout (PDF)”. |
| **NIH Office of Rare Diseases Research (ORDR) – “Rare Disease Information Center”** | Links to disease‑specific fact sheets, including cardiac amyloidosis, and a list of ongoing clinical trials. | <https://rarediseases.info.nih.gov/diseases/10153/amyloidosis> → “Download” button. |
| **National Library of Medicine (NLM) – MedlinePlus “Amyloidosis”** | Easy‑to‑read summary, FAQs, and printable handouts. | <https://medlineplus.gov/amyloidosis.html> → “Print” or “PDF” at the bottom of the page. |
| **NIH Clinical Center – “Patient Education Materials”** | Short brochures on cardiac amyloidosis used in clinical trials (often 3‑page, high‑resolution). | Email the Clinical Center’s patient‑education office (clinicalcenter@nih.gov) requesting “Cardiac Amyloidosis brochure”. They typically respond with PDFs. |

*If you are on the NIH campus you can also visit the **NIH Library** (the “National Library of Medicine” building, 8600 Rockville Pike) and request printed copies of any of the above PDFs at the reference desk.*

---

## 2. Peer‑Reviewed Review Articles (Open‑Access)

These are more detailed but still written in a way that non‑specialists can understand. All are freely downloadable (no subscription required).

| Citation (APA) | Key Points | Link |
|----------------|------------|------|
| Falk, R. H., & Alexander, K. M. (2022). **Cardiac amyloidosis: Evolving concepts and emerging therapies.** *Nature Reviews Cardiology, 19*(5), 287‑301. | Comprehensive overview of AL vs. ATTR amyloidosis, diagnostic algorithm (echo, CMR, nuclear imaging), and FDA‑approved drugs (tafamidis, patisiran, inotersen). | <https://doi.org/10.1038/s41569-022-00673-5> |
| Dorbala, S., & Maurer, M. S. (2023). **Practical approach to the diagnosis of cardiac amyloidosis.** *JACC: Cardiovascular Imaging, 16*(2), 254‑267. | Step‑by‑step flowchart for clinicians; includes printable algorithm (Fig. 2) that works well as a handout. | <https://doi.org/10.1016/j.jcmg.2022.09.009> |
| Rapezzi, C., & Merlini, G. (2021). **Management of transthyretin cardiac amyloidosis.** *European Heart Journal, 42*(44), 4245‑4255. | Focus on ATTR, disease‑modifying agents, and lifestyle recommendations. | <https://doi.org/10.1093/eurheartj/ehaa1025> |

Footnote: merged outputs may be deduplicated by `id` or prompt hash during shard merge.
