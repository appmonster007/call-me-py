"""CASE: overriding, super(), polymorphic calls, inherited (non-overridden) methods, template method.
WHY: self.sound() resolves differently per receiver class.
EXPECTED (ground truth from a traced run; caller -> callee):
  <module> -> Animal.describe
  Animal.describe -> Animal.name
  Animal.describe -> Animal.speak
  Animal.describe -> Dog.name
  Animal.describe -> Puppy.speak
  Animal.speak -> Animal.sound
  Animal.speak -> Cat.sound
  Animal.speak -> Dog.sound
  Dog.name -> Animal.name
  Puppy.speak -> Animal.speak
"""


class Animal:
    def speak(self):
        return self.sound()

    def sound(self):
        return "..."

    def describe(self):
        return self.speak() + self.name()

    def name(self):
        return "animal"


class Dog(Animal):
    def sound(self):
        return "woof"

    def name(self):
        return "dog" + super().name()


class Puppy(Dog):
    def speak(self):
        return super().speak() + "!"


class Cat(Animal):
    def sound(self):
        return "meow"


for pet in (Dog(), Cat(), Puppy()):
    pet.describe()
Animal().describe()
