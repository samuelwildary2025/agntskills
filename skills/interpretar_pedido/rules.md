# Regras de Interpretação de Produto

Estas regras devem ser aplicadas ao interpretar o texto do usuário ANTES de buscar o produto.

## 1. Pesáveis (frutas, legumes, açougue, frios)
Sempre mostre no formato: `Quantidade (peso aproximado)`. Se o cliente pedir em **unidades** para um item vendido por **kg**, estime o peso médio abaixo:

- Laranja, maçã, pera, tomate, batata, cebola, cenoura, beterraba: 0.20kg cada
- Banana: 0.15kg cada
- Limão: 0.10kg cada
- Pão francês: 0.05kg cada
- Pão sovado (massa fina): 0.06kg cada
- Mamão, melão: 1.0kg cada
- Melancia: 8.0kg cada

## 2. Açougue
- Se o cliente falar **"kg"**, respeite o peso exato.
- Se o cliente falar **"peça"** ou **"unidade"**, você deve estimar o peso.
- Se houver ambiguidade (ex: "5 picanhas"), pergunte: "Você quer 5kg ou 5 peças?"
- **Frango inteiro/abatido** em unidades:
  - "frango grande": estimar **3.0kg por unidade**.
  - Exemplo: "2 frangos grandes" -> `unidades=2` e `quantidade=6.0kg`.

## 3. Preparo / Observações
- A palavra **"cortado"** ou "moído" deve ser tratada como *observação* de preparo para o carrinho, não muda a busca pelo produto base.

## 4. Sorvete e Litros
- Se o cliente pedir sorvete em **kg**, converta para **litros** e confirme educadamente que o sorvete é vendido por litro.

## 5. Alho
- "cabeça de alho" -> Tratar como "alho" e estimar 0.05 a 0.06kg por unidade.

## 6. Ovos
- Se pedir "bandeja" ou "cartela" de ovo sem especificar quantidade, o padrão é **20 unidades** (Buscar `ovo branco 20`).
- Só pergunte se houver pedido de quantidade diferente ou se 20 unidades estiver indisponível.

## 7. Tamanhos e Atributos
- Palavras como "grande", "pequeno", "médio", "azul", "vermelho", "tradicional", "original" são atributos. Em muitos casos ajudam no match, mas não definem o produto sozinhos.
