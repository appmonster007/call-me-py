# Project Proposal: Call-me-Py - Python Static Analyzer for Call Graph Construction with Higher-Order Function Support

## Project Idea and Abstract

The objective of this project is to build a robust static program analysis system capable of generating comprehensive call-graphs for Python programs. While traditional static analysis tools can map standard first-order function calls, they frequently struggle with dynamically typed languages and functional programming paradigms. This project will focus on supporting and analyzing calls to higher-order functions (HOFs), and polymorphism.

By taking Python source code as input, the system will output a structured call-graph (e.g., in DOT or JSON format) that tracks function references through variables, parameters, and return values, providing a deeper and more accurate representation of program execution flow. Based on this call graph, we can analyze it further and infer warnings for use vulnerable libraries and functions.

## Motivation

Python’s dynamic behavior and use of higher-order functions, such as decorators, map, filter, custom callbacks, and polymorphism make static code analysis challenging. When standard analysis tools encounter a function passed as a variable, they often drop the trace, resulting in an incomplete or disconnected call-graph. 

This missing information can create blind spots for developers attempting to refactor large codebases, security researchers scanning vulnerabilities via taint analysis, and automated tools performing dead-code elimination for example. By accurately mapping HOFs, this project bridges the gap between dynamic language flexibility and static analysis reliability, directly improving code comprehensibility, maintainability, and security auditing. 

## Technical Approach and Handling Language Features

The core engine will be built using Python’s built-in ast (Abstract Syntax Tree) module to parse source code without executing it. To handle complex language features that are not explicitly defined in simple sequential source material (such as implied built-ins, dynamic binding, and closures), the system will implement the following approaches:

1. **Abstract Interpretation for Variable Tracking**: Instead of just looking at explicit function calls (`foo()`), the parser will track variable assignments where functions are treated as objects (`x = foo`). We will implement a lightweight environment/scope tracker during the AST traversal to remember which function object a variable currently binds to.

2. **Decorator Unrolling**: Python decorators syntactically wrap functions. The analysis engine will explicitly resolve the `@decorator` syntax into its underlying higher-order function equivalent (`func = decorator(func)`) during the AST pass to accurately trace the execution path.

3. Lambda and Built-in HOF Modeling: Anonymous functions (lambdas) will be assigned unique internal identifiers in the call-graph. We will also build a small repository of stubs for common standard library HOFs (like `map`, `filter`, and `reduce`) so the analyzer knows how they invoke their arguments without needing to parse the Python runtime source C-code.

## Milestones and Expected Timeline

The project will be executed over a four-week period following the proposal submission, broken down into distinct, measurable milestones.

### Phase 1: Foundation (Nov 6 – Nov 12)

Implement standard AST parsing. Generate basic call-graphs for simple, first-order function calls. Output results to visual DOT format.

### Phase 2: HOF Integration (Nov 13 – Nov 21)

Implement scope tracking. Map variables to function references. Support standard decorators, lambdas, and closures.

### Phase 3: Testing & Edge Cases (Nov 22 – Nov 28)

AI-assisted test generation. Resolve edge cases (nested functions, built-in HOFs like map). Finalize graph accuracy.

### Phase 4: Wrap-up & Deliverables (Nov 29 – Dec 4)

Feature freeze. Draft the final project report. Prepare slides and rehearse the final presentation.

## AI Interaction Strategy

To execute this project efficiently, AI assistants will be integrated into the development workflow as collaborative thought partners and productivity accelerators:

1. **Boilerplate Generation**: AI will be used to generate the repetitive NodeVisitor classes required for parsing the dozens of distinct node types in Python’s ast module, saving hours of manual coding.

2. **Edge-Case Brainstorming**: Python has a massive surface area of syntactic sugar. AI will be utilized to brainstorm edge cases and obscure ways developers pass functions around, ensuring the analyzer's scope tracking is robust.

3. **Documentation**: AI will assist in generating docstrings, standardizing code comments, and drafting the initial structure for the final presentation based on the completed code.

## Testing Strategy

Given the complexity of static analysis, a robust testing strategy is required to build trust in the tool's accuracy. We will leverage AI to assist in generating a comprehensive test suite using pytest.

1. **Unit Testing**: AI will be prompted to generate hundreds of small, isolated Python snippets containing specific language features (e.g., a file with just a lambda, a file with a nested decorator). The unit tests will verify that the AST visitor correctly extracts the exact nodes and identifies the caller/callee pair for that isolated feature.
 
2. **End-to-End (E2E) Testing**: We will curate a set of "Gold Standard" Python scripts (ranging from 50 to 500 lines) containing complex, real-world combinations of HOFs and object-oriented patterns. We will manually define the expected JSON output of the call-graph. The E2E tests will run the analyzer against these scripts and assert that the generated graph matches the Gold Standard perfectly. 

3. **Continuous Integration**: Tests will be run continuously as new AST node support is added to ensure that fixing a closure bug doesn't break basic first-order function mapping.

## Extension possibilites

- **implicit calls**
    - magic methods (operators/built-ins)
    - attribute access interception
    - class hooks
    - keywords (`with`, `in`, `yield`)
- **object mutation (swapping class methods)**
- dynamic property lookups (`getattr`, `__dict__`)
- dynamic type creation
