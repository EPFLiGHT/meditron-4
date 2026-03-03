# Distillation Output Examples (MedGemma, 2026-03-02 Run)

Source file: `distillation/distilled_outputs/distill_test_google_medgemma-27b-text-it_20260302T105522Z.jsonl`

One representative MedGemma distilled sample per dataset (from the 2-example distillation test output).

| dataset | source_rows | sampled_rows | distilled_rows | sample_coverage | empty_answer_rate | finish_reason_counts | notes |
|---|---:|---:|---:|---:|---:|---|---|
| afrimedqa_v1_test_3000_with_labels.jsonl | 3000 | 2 | 2 | 100.00% | 100.00% | stop:2 | 2 empty answers |
| afrimedqa_v2_full_15275_with_labels.jsonl | 15275 | 2 | 2 | 100.00% | 50.00% | stop:2 | 1 empty answers |
| healthsearchqa_3375_no_labels.jsonl | 3375 | 2 | 2 | 100.00% | 0.00% | stop:2 | - |
| liveqa_truehealth_635_with_labels.jsonl | 635 | 2 | 2 | 100.00% | 0.00% | stop:2 | - |
| medexpqa_train_434.jsonl | 434 | 2 | 2 | 100.00% | 0.00% | stop:2 | - |
| medmcqa_with_labels.jsonl | 182822 | 2 | 2 | 100.00% | 0.00% | stop:2 | - |
| medqa_with_labels_5opt.jsonl | 10178 | 2 | 2 | 100.00% | 0.00% | stop:2 | - |
| pubmedqa_pqa_labeled_1000_medgemma_style_with_labels.jsonl | 1000 | 2 | 2 | 100.00% | 0.00% | stop:2 | - |

## Examples

### afrimedqa_v1_test_3000_with_labels.jsonl

- id: `265049d633cd0f3229d266a5284aa104eba52196034cb16afe7a0a70bac253f6`
- true_label: `option4`
- source_row_index: `0`
- question_preview: Question: A 34-year-old man presents with a 1-week history of muscle aches, fatigue, and fever that occurs every 2 days. His history is significant for a trip to Nepal. Vital signs...
- distilled_model: `google/medgemma-27b-text-it`
- distilled_timestamp_utc: `2026-03-02T10:55:08.999736+00:00`
- distilled_finish_reason: `stop`

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

_(empty distilled answer)_

### afrimedqa_v2_full_15275_with_labels.jsonl

- id: `ff784abc6b461a6a15d7f8bf5e6560d3393474fe4748699ff4c7f2124292b95f`
- true_label: `None`
- source_row_index: `0`
- question_preview: Prompt: Your female friend complains of weight gain, high blood pressure and thinks she has Lupus Nephritis and is going to visit the nearest doctor. Question: Question: Can you pr...
- distilled_model: `google/medgemma-27b-text-it`
- distilled_timestamp_utc: `2026-03-02T10:55:08.999837+00:00`
- distilled_finish_reason: `stop`

#### Source answer(s)

- true_label: `None`
_(no source assistant answer found for this sample)_

#### Distilled answer

_(empty distilled answer)_

### healthsearchqa_3375_no_labels.jsonl

- id: `1`
- true_label: `None`
- source_row_index: `0`
- question_preview: Are benign brain tumors serious?
- distilled_model: `google/medgemma-27b-text-it`
- distilled_timestamp_utc: `2026-03-02T10:55:08.999895+00:00`
- distilled_finish_reason: `stop`

#### Source answer(s)

- true_label: `None`
_(no source assistant answer found for this sample)_

#### Distilled answer

Yes, benign brain tumors can be serious.

### liveqa_truehealth_635_with_labels.jsonl

- id: `1`
- true_label: `Cardiac amyloidosis is a disorder caused by deposits of an abnormal protein (amyloid) in the heart tissue. These deposits make it hard for the heart to work properly.`
- source_row_index: `0`
- question_preview: Literature on Cardiac amyloidosis. Please let me know where I can get literature on Cardiac amyloidosis. My uncle died yesterday from this disorder. Since this is such a rare disor...
- distilled_model: `google/medgemma-27b-text-it`
- distilled_timestamp_utc: `2026-03-02T10:55:08.999947+00:00`
- distilled_finish_reason: `stop`

#### Source answer(s)

- true_label: `Cardiac amyloidosis is a disorder caused by deposits of an abnormal protein (amyloid) in the heart tissue. These deposits make it hard for the heart to work properly.`
- source_assistant_answer:

> Cardiac amyloidosis is a disorder caused by deposits of an abnormal protein (amyloid) in the heart tissue. These deposits make it hard for the heart to work properly.

#### Distilled answer

Here are several resources where you can find literature on cardiac amyloidosis, including options for obtaining physical copies:

1.  **Amyloidosis Foundation:** This is a leading patient advocacy and research organization. They offer a wide range of brochures, fact sheets, and educational materials specifically about amyloidosis, including cardiac amyloidosis. They often have materials available for distribution.
    *   **Website:** [https://www.amyloidosis.org/](https://www.amyloidosis.org/)
    *   **Contact:** You can contact them directly to inquire about bulk orders or materials suitable for distribution at a funeral service. They may be able to mail materials to you.

2.  **Amyloidosis Research Consortium (ARC):** ARC focuses on funding research and providing information. Their website has resources for patients and families.
    *   **Website:** [https://www.arci.org/](https://www.arci.org/)
    *   **Contact:** Similar to the Amyloidosis Foundation, they may have printable resources or be able to assist with obtaining materials.

3.  **National Heart, Lung, and Blood Institute (NHLBI) - Part of NIH:** As a former NIH employee, you are familiar with the campus. While NHLBI doesn't typically maintain a public distribution center for printed materials like brochures *specifically* on cardiac amyloidosis for walk-in pickup, they have extensive information online. You could potentially contact their health information office to see if they have any suitable materials or can direct you to relevant resources within NIH.
    *   **Website:** [https://www.nhlbi.nih.gov/](https://www.nhlbi.nih.gov/) (Search for "cardiac amyloidosis")
    *   **Contact:** NHLBI Health Information Office: (301) 496-4422 or NHLBIinfo@mail.nih.gov. Explain your situation and ask if they have any printed materials available or can suggest resources.

4.  **American Heart Association (AHA):** The AHA provides information on various heart conditions, including amyloidosis. They may have fact sheets or articles available.
    *   **Website:** [https://www.heart.org/](https://www.heart.org/) (Search for "cardiac amyloidosis")
    *   **Contact:** They may offer downloadable resources or have a publications department you can contact.

5.  **Mayo Clinic & Cleveland Clinic:** These major medical centers have comprehensive information on cardiac amyloidosis available on their websites, often written in patient-friendly language. You can print articles or fact sheets directly from their sites.
    *   **Mayo Clinic:** [https://www.mayoclinic.org/diseases-conditions/amyloidosis/symptoms-causes/syc-20353929](https://www.mayoclinic.org/diseases-conditions/amyloidosis/symptoms-causes/syc-20353929)
    *   **Cleveland Clinic:** [https://my.clevelandclinic.org/health/diseases/17757-cardiac-amyloidosis](https://my.clevelandclinic.org/health/diseases/17757-cardiac-amyloidosis)

**Recommendations for Funeral Service:**

*   **Focus on Patient-Friendly Materials:** Choose materials that are easy to understand for a general audience, avoiding overly technical jargon.
*   **Contact Organizations Directly:** The Amyloidosis Foundation is likely your best bet for obtaining printed materials specifically designed for patients and families. Explain your purpose when contacting them; they may offer assistance or discounts.
*   **Print Online Resources:** Websites like Mayo Clinic and Cleveland Clinic offer articles and fact sheets that you can easily print.
*   **Consider a Brief Summary:** You might also consider creating a short, simple handout summarizing what cardiac amyloidosis is, its impact, and perhaps a link to the Amyloidosis Foundation website for those seeking more information.

Given your familiarity with NIH, contacting the NHLBI Health Information Office is a reasonable first step, but the Amyloidosis Foundation is the most specialized resource for this condition. My condolences on the loss of your uncle.

### medexpqa_train_434.jsonl

- id: `None`
- true_label: `None`
- source_row_index: `0`
- question_preview: Juan, a second year resident, attends Sofia, a 15 year old girl in the emergency room, who apparently fainted at school without losing consciousness. The patient says that she was...
- distilled_model: `google/medgemma-27b-text-it`
- distilled_timestamp_utc: `2026-03-02T10:55:08.999180+00:00`
- distilled_finish_reason: `stop`

#### Source answer(s)

- source_assistant_answer:

> There is no doubt that the doctor-patient relationship today transcends the physical environment of the consultation room. But we must not forget that we must maintain the same ethical and professional codes as in the real environment. The code of ethics Article 26-3. The clinical practice of medicine by means of consultations exclusively by letter, telephone, radio, press or internet, is contrary to the deontological norms. [HIDDEN] the personal and direct contact between the doctor and the patient.

#### Distilled answer

"I appreciate the offer, Sofia, but it's best to keep our relationship professional. I'm here to take care of your health, and that's my priority. Let's focus on getting you feeling better."

### medmcqa_with_labels.jsonl

- id: `301b5d37-ac36-4429-98aa-16a6ca7f0669`
- true_label: `C`
- source_row_index: `0`
- question_preview: Schistosomiasis is transmitted by ? A. Cyclops B. Fish C. Snaile D. Cattle
- distilled_model: `google/medgemma-27b-text-it`
- distilled_timestamp_utc: `2026-03-02T10:55:08.997881+00:00`
- distilled_finish_reason: `stop`

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

C

### medqa_with_labels_5opt.jsonl

- id: `1`
- true_label: `E`
- source_row_index: `0`
- question_preview: Question: A 23-year-old pregnant woman at 22 weeks gestation presents with burning upon urination. She states it started 1 day ago and has been worsening despite drinking more wate...
- distilled_model: `google/medgemma-27b-text-it`
- distilled_timestamp_utc: `2026-03-02T10:55:08.990747+00:00`
- distilled_finish_reason: `stop`

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

E. Nitrofurantoin

### pubmedqa_pqa_labeled_1000_medgemma_style_with_labels.jsonl

- id: `21645374`
- true_label: `yes`
- source_row_index: `0`
- question_preview: Context: Programmed cell death (PCD) is the regulated death of cells within an organism. The lace plant (Aponogeton madagascariensis) produces perforations in its leaves through PC...
- distilled_model: `google/medgemma-27b-text-it`
- distilled_timestamp_utc: `2026-03-02T10:55:08.998540+00:00`
- distilled_finish_reason: `stop`

#### Source answer(s)

- true_label: `yes`
- true_label_abc: `A`
- source_assistant_answer:

> Results depicted mitochondrial dynamics in vivo as PCD progresses within the lace plant, and highlight the correlation of this organelle with other organelles during developmental PCD. To the best of our knowledge, this is the first report of mitochondria and chloroplasts moving on transvacuolar strands to form a ring structure surrounding the nucleus during developmental PCD. Also, for the first time, we have shown the feasibility for the use of CsA in a whole plant system. Overall, our findings implicate the mitochondria as playing a critical and early role in developmentally regulated PCD in the lace plant.

#### Distilled answer

Yes
